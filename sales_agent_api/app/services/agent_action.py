"""Agent action validation service.

After the LLM produces a response, n8n calls this service to:
  1. Persist strategy-relevant extracted_data into conversation.extracted_context
     (with DAG gates to enforce data order: user_confirmation needs
     name+phone+address+city; payment_confirmation needs user_confirmation+phone+address)
  2. Merge stable customer facts back into client_users.profile (persistent).
  3. On payment_confirmation, bump profile.purchase_count + append purchase record.
  4. Auto-escalate to human_handoff when all purchase data is collected.
  5. Apply a proposed_transition if the LLM sends one (rare).
  6. Persist the outbound message with AI metadata.
  7. Write audit log entries.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditLog, Client, ClientUser, Conversation, Message, Product
from app.services.field_validators import validate_extracted_data
from app.services.goal_strategy import GoalStrategyEngine
from app.services.state_machine import (
    InvalidTransitionError,
    validate_transition,
)

logger = logging.getLogger(__name__)

# Fields from extracted_data that are persisted to conversation.extracted_context
# so the GoalStrategyEngine can track progress across turns.
STRATEGY_FIELDS = {
    "product_id", "full_name", "phone",
    "shipping_address", "shipping_city",
    "user_confirmation", "payment_confirmation",
}

# Subset of extracted_context fields that describe the person, not the
# in-flight order — these get merged back to client_users.profile so the
# next conversation starts already knowing them.
PROFILE_PERSIST_MAP = {
    "full_name": "full_name",
    "phone": "phone",
    "shipping_address": "shipping_address",
    "shipping_city": "city",
    "email": "email",
}

# Lifecycle stage ordering — only allow forward transitions to avoid
# accidentally demoting a customer back to engaged on a follow-up chat.
_LIFECYCLE_RANK = {"new": 0, "engaged": 1, "customer": 2, "dormant": 1}


class AgentActionError(Exception):
    pass


class ConversationNotFoundError(AgentActionError):
    pass


class StaleContextError(AgentActionError):
    pass


async def process_agent_action(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    strategy_version: int,
    response_text: str,
    proposed_transition: Optional[str] = None,
    extracted_data: Optional[dict] = None,
    ai_model: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> dict:
    """Validate and persist an agent turn.

    Returns dict with: approved, final_response_text, new_state, side_effects.
    """
    extracted_data = extracted_data or {}
    side_effects: list[str] = []
    now = datetime.now(timezone.utc)

    # --- 1. Load conversation ------------------------------------------------
    conv_row = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.client_id == client_id,
        )
    )
    conversation: Optional[Conversation] = conv_row.scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFoundError(
            f"Conversation {conversation_id} not found for client {client_id}"
        )

    # --- 2. Stale context check ----------------------------------------------
    if conversation.strategy_version != strategy_version:
        raise StaleContextError(
            f"strategy_version mismatch: expected {conversation.strategy_version}, "
            f"got {strategy_version}"
        )

    # --- 3. Persist extracted_data → extracted_context with DAG gates --------
    if extracted_data:
        # 3a. Field-level validators (rejects ill-formed values like
        # phone="123456" or shipping_city="acá" before they pollute anything)
        catalog_rows = await session.execute(
            select(Product.id).where(
                Product.client_id == client_id,
                Product.is_available.is_(True),
            )
        )
        valid_product_ids = {str(pid) for pid in catalog_rows.scalars().all()}
        extracted_data, rejection_warnings = validate_extracted_data(
            extracted_data, valid_product_ids=valid_product_ids
        )
        if rejection_warnings:
            side_effects.extend(rejection_warnings)
            for w in rejection_warnings:
                logger.warning("Field validation rejected: %s", w)

        # 3b. Image-already-sent gate: the system_prompt forbids sending the
        # product photo more than once per conversation, but during message
        # bursts two LLM cycles can run in parallel without seeing each
        # other's outbound and both emit send_image_url. Drop it if any
        # prior outbound in this conversation already carried it.
        if extracted_data.get("send_image_url"):
            prior = await session.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "outbound",
                    Message.extracted_data["send_image_url"].astext.isnot(None),
                )
                .limit(1)
            )
            image_already_sent = prior.scalar_one_or_none() is not None
            extracted_data, image_warnings = _filter_send_image_url(
                extracted_data, image_already_sent=image_already_sent
            )
            if image_warnings:
                side_effects.extend(image_warnings)
                logger.warning(
                    "Dropped send_image_url for conversation %s: prior outbound already carried it",
                    conversation.id,
                )

        strategy_updates = {
            k: v for k, v in extracted_data.items()
            if k in STRATEGY_FIELDS and v
        }
        if strategy_updates:
            current_context = conversation.extracted_context or {}
            merged = {**current_context, **strategy_updates}
            # user_confirmation requires full_name + phone + shipping_address + shipping_city
            if "user_confirmation" in strategy_updates:
                missing = [f for f in ("full_name", "phone", "shipping_address", "shipping_city") if not merged.get(f)]
                if missing:
                    del strategy_updates["user_confirmation"]
                    logger.warning("Rejected user_confirmation: missing %s", missing)
                    side_effects.append(f"warning:premature_summary_missing_{'+'.join(missing)}")
            # payment_confirmation requires user_confirmation + phone + shipping_address
            if "payment_confirmation" in strategy_updates:
                missing = [f for f in ("user_confirmation", "phone", "shipping_address") if not merged.get(f)]
                if missing:
                    del strategy_updates["payment_confirmation"]
                    logger.warning("Rejected payment_confirmation: missing %s", missing)
        if strategy_updates:
            new_context = {**(conversation.extracted_context or {}), **strategy_updates}
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(extracted_context=new_context)
            )
            conversation.extracted_context = new_context
            side_effects.append(f"context_updated:{list(strategy_updates.keys())}")

            # Merge stable customer facts into the persistent profile
            payment_just_confirmed = "payment_confirmation" in strategy_updates
            await _merge_profile(
                session,
                client_user_id=conversation.client_user_id,
                extracted_context=new_context,
                payment_just_confirmed=payment_just_confirmed,
            )

            # CRM lifecycle bump: any accepted slot moves a 'new' user to
            # 'engaged'. A confirmed payment moves them to 'customer'.
            target_stage = "customer" if payment_just_confirmed else "engaged"
            await _bump_lifecycle_stage(
                session,
                client_user_id=conversation.client_user_id,
                target=target_stage,
            )

    # --- 4. Auto-escalate when all purchase data is collected ----------------
    if conversation.state != "human_handoff":
        client_row = await session.execute(
            select(Client).where(Client.id == client_id)
        )
        client = client_row.scalar_one_or_none()
        business_rules = (client.business_rules or {}) if client else {}
        collected_data = conversation.extracted_context or {}
        goal = conversation.active_goal or business_rules.get("default_goal", "close_sale")
        directive = GoalStrategyEngine().compute(goal, collected_data, business_rules)
        if directive.all_complete:
            old_state = conversation.state
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(state="human_handoff")
            )
            conversation.state = "human_handoff"
            session.add(
                AuditLog(
                    client_id=client_id,
                    event_type="auto_escalated",
                    entity_type="conversation",
                    entity_id=conversation.id,
                    actor_type="system",
                    new_value={
                        "old_state": old_state,
                        "new_state": "human_handoff",
                        "reason": "purchase_data_complete",
                    },
                )
            )
            side_effects.append("escalated:purchase_data_complete")
            logger.info(
                "Auto-escalated conversation %s: all checkpoints complete",
                conversation.id,
            )

        # Refresh strategy snapshot so the conversation row reflects
        # the post-merge state (was H4 in the May 2 review: snapshot stayed
        # at the value from the previous ingest, showing 60% progress on a
        # conversation that had auto-escalated).
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(
                current_checkpoint=directive.current_checkpoint,
                progress_pct=directive.progress_pct,
                strategy_snapshot={
                    "goal": directive.goal,
                    "progress_pct": directive.progress_pct,
                    "current_checkpoint": directive.current_checkpoint,
                    "missing_fields": directive.missing_fields,
                    "completed_checkpoints": directive.completed_checkpoints,
                },
            )
        )
        conversation.current_checkpoint = directive.current_checkpoint
        conversation.progress_pct = directive.progress_pct

    # --- 5. Apply proposed_transition (if any) -------------------------------
    if proposed_transition and proposed_transition != conversation.state:
        try:
            validate_transition(conversation.state, proposed_transition)
        except InvalidTransitionError as exc:
            logger.warning("Transition rejected: %s", exc)
            side_effects.append(f"transition_rejected:{conversation.state}→{proposed_transition}")
        else:
            old_state = conversation.state
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(state=proposed_transition)
            )
            conversation.state = proposed_transition
            side_effects.append(f"state_changed:{old_state}→{proposed_transition}")
            session.add(
                AuditLog(
                    client_id=client_id,
                    event_type="state_transition",
                    entity_type="conversation",
                    entity_id=conversation.id,
                    actor_type="agent",
                    new_value={"old_state": old_state, "new_state": proposed_transition},
                )
            )

    # --- 6. Persist outbound message -----------------------------------------
    out_message = Message(
        conversation_id=conversation.id,
        client_id=client_id,
        direction="outbound",
        message_type="text",
        content=response_text,
        ai_model_used=ai_model,
        ai_prompt_tokens=prompt_tokens,
        ai_completion_tokens=completion_tokens,
        ai_latency_ms=latency_ms,
        extracted_data=extracted_data,
    )
    session.add(out_message)
    await session.flush()

    session.add(
        AuditLog(
            client_id=client_id,
            event_type="agent_turn",
            entity_type="message",
            entity_id=out_message.id,
            actor_type="agent",
            new_value={"side_effects": side_effects},
        )
    )

    await session.flush()

    return {
        "approved": True,
        "final_response_text": response_text,
        "new_state": conversation.state,
        "side_effects": side_effects,
        "rejection_reason": None,
    }


async def _merge_profile(
    session: AsyncSession,
    client_user_id: uuid.UUID,
    extracted_context: dict,
    payment_just_confirmed: bool,
) -> None:
    """Merge stable customer facts from extracted_context into client_users.profile.

    On payment_confirmation, also increments purchase_count and appends a
    lightweight purchase record.
    """
    updates: dict = {}
    for ctx_key, profile_key in PROFILE_PERSIST_MAP.items():
        val = extracted_context.get(ctx_key)
        if val:
            updates[profile_key] = val

    # Derive first_name from full_name if present
    full_name = extracted_context.get("full_name")
    if full_name:
        updates["first_name"] = str(full_name).split()[0]

    if not updates and not payment_just_confirmed:
        return

    cu_row = await session.execute(
        select(ClientUser).where(ClientUser.id == client_user_id)
    )
    client_user = cu_row.scalar_one_or_none()
    if client_user is None:
        return

    profile = dict(client_user.profile or {})
    profile.update(updates)

    if payment_just_confirmed:
        profile["purchase_count"] = int(profile.get("purchase_count", 0)) + 1
        purchases = list(profile.get("purchases", []))
        purchases.append({
            "date": datetime.now(timezone.utc).isoformat(),
            "product_id": extracted_context.get("product_id"),
        })
        profile["purchases"] = purchases

    await session.execute(
        update(ClientUser)
        .where(ClientUser.id == client_user_id)
        .values(profile=profile)
    )


def _filter_send_image_url(
    extracted_data: dict,
    image_already_sent: bool,
) -> tuple[dict, list[str]]:
    """Drop send_image_url from extracted_data if a prior outbound in the
    same conversation already carried one. Returns (clean_data, warnings).

    Pure function so it can be unit-tested without a DB. The DB query that
    determines image_already_sent lives in process_agent_action.
    """
    if not extracted_data.get("send_image_url"):
        return extracted_data, []
    if not image_already_sent:
        return extracted_data, []
    clean = {k: v for k, v in extracted_data.items() if k != "send_image_url"}
    return clean, ["warning:image_already_sent"]


async def _bump_lifecycle_stage(
    session: AsyncSession,
    client_user_id: uuid.UUID,
    target: str,
) -> None:
    """Move the customer forward in the CRM lifecycle. Never downgrades —
    a customer who comes back for support stays a customer."""
    target_rank = _LIFECYCLE_RANK.get(target, 0)
    cu_row = await session.execute(
        select(ClientUser.lifecycle_stage).where(ClientUser.id == client_user_id)
    )
    current = cu_row.scalar_one_or_none()
    if current is None:
        return
    current_rank = _LIFECYCLE_RANK.get(current, 0)
    if target_rank <= current_rank:
        return
    await session.execute(
        update(ClientUser)
        .where(ClientUser.id == client_user_id)
        .values(lifecycle_stage=target)
    )
