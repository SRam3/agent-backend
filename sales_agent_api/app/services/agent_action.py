"""Agent action validation service.

After the LLM produces a response, n8n calls this service to:
  0. Circuit breaker (P8): if the response would be the 3rd consecutive
     identical outbound, escalate to human_handoff and suppress it
     (approved=False, empty final_response_text) instead of persisting.
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
from app.services.goal_strategy import GoalStrategyEngine
from app.services.state_machine import (
    InvalidTransitionError,
    validate_transition,
)
from app.services.validation import is_plausible_phone

logger = logging.getLogger(__name__)

# Fields from extracted_data that are persisted to conversation.extracted_context
# so the GoalStrategyEngine can track progress across turns. These ARE the DAG
# checkpoints — adding to this set changes close_sale behaviour.
STRATEGY_FIELDS = {
    "product_id", "full_name", "phone",
    "shipping_address", "shipping_city",
    "user_confirmation", "payment_confirmation",
}

# Non-DAG order details the customer volunteers (quantity, grind/roast taste).
# Persisted to extracted_context in parallel to STRATEGY_FIELDS so the bot stops
# re-asking what was already said — but the GoalStrategyEngine never treats them
# as checkpoints (it only reads its own required_fields), so the DAG is untouched.
ORDER_FIELDS = {
    "quantity", "grind_preference", "roast_preference",
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


# DAG gate requirements — kept here so the pure selector below and the caller
# share one source of truth.
_USER_CONFIRMATION_REQUIRES = ("full_name", "phone", "shipping_address", "shipping_city")
_PAYMENT_CONFIRMATION_REQUIRES = ("user_confirmation", "phone", "shipping_address")

# Circuit breaker (P8): the outbound about to be sent fires the breaker when it
# is the 3rd consecutive identical response — it equals BOTH of the two most
# recent outbounds of the same conversation.
_LOOP_PREVIOUS_OUTBOUNDS = 2
LOOP_SIDE_EFFECT = "circuit_breaker:loop_detected"


def detect_outbound_loop(
    candidate_text: str,
    previous_outbound_texts: list[Optional[str]],
) -> bool:
    """True when ``candidate_text`` would be the 3rd consecutive identical
    outbound: both of the two most recent outbounds match it EXACTLY.

    Exact (``==``) comparison by design — no normalisation, no fuzziness — so
    legitimately similar responses never trip it. A repeated-but-not-consecutive
    response (A, B, A) never trips it either: with B in between, the two most
    recent are (B, A), which can't both equal A.

    Pure — no I/O. ``previous_outbound_texts`` is expected newest-first.
    """
    if len(previous_outbound_texts) < _LOOP_PREVIOUS_OUTBOUNDS:
        return False
    return all(
        text == candidate_text
        for text in previous_outbound_texts[:_LOOP_PREVIOUS_OUTBOUNDS]
    )


def _recent_outbound_stmt(client_id: uuid.UUID, conversation_id: uuid.UUID):
    """Statement for the 2 most recent outbound texts of ONE conversation,
    newest-first. Kept as a pure builder so tests can assert the tenant
    filters (client_id + conversation_id) without a live DB."""
    return (
        select(Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.client_id == client_id,
            Message.direction == "outbound",
        )
        .order_by(Message.created_at.desc())
        .limit(_LOOP_PREVIOUS_OUTBOUNDS)
    )


def compute_context_updates(
    extracted_data: dict,
    current_context: dict,
) -> tuple[dict, dict, list[dict]]:
    """Decide which extracted_data fields get merged into extracted_context.

    Pure — no I/O. Lets the persistence decision (including DAG gates) be unit
    tested without a session.

    Returns ``(accepted, strategy_accepted, rejections)``:
      - ``accepted``: every field to merge (ORDER_FIELDS + STRATEGY_FIELDS that
        passed their gates).
      - ``strategy_accepted``: the subset that are DAG strategy fields — drives
        profile merge + lifecycle bump in the caller. ORDER_FIELDS never appear
        here, so they can't trip the engine or the CRM lifecycle.
      - ``rejections``: ``[{"field", "missing"}]`` for gated slots dropped
        because their prerequisites weren't met yet.
    """
    order_updates = {k: v for k, v in extracted_data.items() if k in ORDER_FIELDS and v}
    strategy_updates = {k: v for k, v in extracted_data.items() if k in STRATEGY_FIELDS and v}
    rejections: list[dict] = []

    # phone must be a plausible international number (E.164-lax, ADR-008):
    # 7-15 digits. Gated BEFORE merged is computed so garbage rejected this
    # turn can't count toward user_confirmation/payment prerequisites either.
    # The rejection carries no phone value — it must never reach logs.
    if "phone" in strategy_updates and not is_plausible_phone(strategy_updates["phone"]):
        del strategy_updates["phone"]
        rejections.append({"field": "phone", "missing": ["plausible_format"]})

    # user_confirmation requires full_name + phone + shipping_address + shipping_city
    merged = {**current_context, **order_updates, **strategy_updates}
    if "user_confirmation" in strategy_updates:
        missing = [f for f in _USER_CONFIRMATION_REQUIRES if not merged.get(f)]
        if missing:
            del strategy_updates["user_confirmation"]
            rejections.append({"field": "user_confirmation", "missing": missing})

    # payment_confirmation requires user_confirmation + phone + shipping_address.
    # Recompute merged AFTER the user_confirmation gate: a user_confirmation that
    # was rejected THIS turn has just been dropped from strategy_updates, so it
    # must not count toward payment's prerequisite. (A user_confirmation already
    # persisted in current_context from a prior turn still counts — it survives
    # in merged via the current_context spread.)
    merged = {**current_context, **order_updates, **strategy_updates}
    if "payment_confirmation" in strategy_updates:
        missing = [f for f in _PAYMENT_CONFIRMATION_REQUIRES if not merged.get(f)]
        if missing:
            del strategy_updates["payment_confirmation"]
            rejections.append({"field": "payment_confirmation", "missing": missing})

    accepted = {**order_updates, **strategy_updates}
    return accepted, strategy_updates, rejections


def is_new_user_confirmation(strategy_accepted: dict, prior_context: dict) -> bool:
    """True only on the turn user_confirmation FIRST lands (ADR-009 §2).

    Pure — the transition (not the state) is what triggers the operator's
    pre-payment notice; the LLM re-proposes the full cumulative extracted_data
    every turn, so presence alone would re-fire on every later turn.
    """
    return (
        "user_confirmation" in strategy_accepted
        and not prior_context.get("user_confirmation")
    )


def _coerce_int(value) -> Optional[int]:
    """Best-effort int from an LLM-supplied value (may be int or string)."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _build_purchase_record(
    extracted_context: dict,
    product_price,
    conversation_id: uuid.UUID,
    now: datetime,
) -> dict:
    """Build a purchase record matching the migration-008 profile contract:
    ``{date, product_id, quantity, total, conversation_id}``.

    ``total`` is ``quantity * product_price`` when both are known, else None.
    Pure — no I/O.
    """
    quantity = _coerce_int(extracted_context.get("quantity"))
    total = None
    if quantity is not None and product_price is not None:
        total = float(product_price * quantity)
    return {
        "date": now.isoformat(),
        "product_id": extracted_context.get("product_id"),
        "quantity": quantity,
        "total": total,
        "conversation_id": str(conversation_id),
    }


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

    # --- 2.5 Circuit breaker: 3rd consecutive identical outbound (P8) --------
    # Checked BEFORE persisting anything from this turn: a looping turn is
    # aborted whole (no context merge, no outbound persisted — the messages
    # trail only records what was actually sent; the two looping outbounds
    # stay the most recent, so an LLM that keeps insisting stays suppressed).
    # NOTE: n8n must check approved/final_response_text before sending — that
    # cut is a known dependency, not implemented here.
    previous_rows = await session.execute(
        _recent_outbound_stmt(client_id, conversation.id)
    )
    if detect_outbound_loop(response_text, list(previous_rows.scalars().all())):
        old_state = conversation.state
        if conversation.state == "active":
            # Same escalation path as the DAG auto-escalate below —
            # active → human_handoff is already valid in state_machine.py.
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(state="human_handoff")
            )
            conversation.state = "human_handoff"
        # If already in human_handoff, no new transition — but the identical
        # response is still suppressed (n8n doesn't cut on state yet).
        side_effects.append(LOOP_SIDE_EFFECT)
        session.add(
            AuditLog(
                client_id=client_id,
                event_type="circuit_breaker",
                entity_type="conversation",
                entity_id=conversation.id,
                actor_type="system",
                new_value={
                    "old_state": old_state,
                    "new_state": conversation.state,
                    "reason": "loop_detected",
                    "identical_count": _LOOP_PREVIOUS_OUTBOUNDS + 1,
                },
            )
        )
        await session.flush()
        logger.warning(
            "Circuit breaker fired on conversation %s: 3rd consecutive "
            "identical outbound suppressed",
            conversation.id,
        )
        return {
            "approved": False,
            "final_response_text": "",
            "new_state": conversation.state,
            "side_effects": side_effects,
            "rejection_reason": "loop_detected",
        }

    # --- 3. Persist extracted_data → extracted_context with DAG gates --------
    if extracted_data:
        accepted, strategy_accepted, rejections = compute_context_updates(
            extracted_data, conversation.extracted_context or {}
        )
        for rej in rejections:
            logger.warning("Rejected %s: missing %s", rej["field"], rej["missing"])
            # Surface a premature user_confirmation as a side-effect so n8n/ops
            # can see the bot tried to summarise before data was complete.
            if rej["field"] == "user_confirmation":
                side_effects.append(
                    f"warning:premature_summary_missing_{'+'.join(rej['missing'])}"
                )
            # Likewise surface a premature payment_confirmation. This is the money
            # step, and the human who inherits the handoff must SEE that a payment
            # was attempted and rejected rather than have it vanish silently. Kept
            # as a distinct string from the summary warning above.
            elif rej["field"] == "payment_confirmation":
                side_effects.append(
                    f"warning:premature_payment_missing_{'+'.join(rej['missing'])}"
                )
            # An implausible phone (ADR-008): dropped, not persisted. The bot
            # keeps the conversation going (fail-safe) and ops sees the drop.
            elif rej["field"] == "phone":
                side_effects.append("warning:invalid_phone_rejected")
        if accepted:
            prior_context = conversation.extracted_context or {}
            newly_user_confirmed = is_new_user_confirmation(
                strategy_accepted, prior_context
            )
            new_context = {**prior_context, **accepted}
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(extracted_context=new_context)
            )
            conversation.extracted_context = new_context
            side_effects.append(f"context_updated:{list(accepted.keys())}")
            if newly_user_confirmed:
                side_effects.append("checkpoint_completed:user_confirmed")

            # Profile merge + CRM lifecycle bump are driven ONLY by DAG strategy
            # fields — order details (quantity/grind/roast) never move lifecycle.
            if strategy_accepted:
                payment_just_confirmed = "payment_confirmation" in strategy_accepted
                product_price = None
                if payment_just_confirmed:
                    product_price = await _fetch_product_price(
                        session, client_id, new_context.get("product_id")
                    )
                # Merge stable customer facts into the persistent profile
                await _merge_profile(
                    session,
                    client_user_id=conversation.client_user_id,
                    extracted_context=new_context,
                    payment_just_confirmed=payment_just_confirmed,
                    conversation_id=conversation.id,
                    product_price=product_price,
                )

                # CRM lifecycle bump: any accepted strategy slot moves a 'new'
                # user to 'engaged'. A confirmed payment moves them to 'customer'.
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


async def _fetch_product_price(
    session: AsyncSession,
    client_id: uuid.UUID,
    product_id,
) -> Optional[object]:
    """Look up a product's unit price (Decimal) for the purchase total.
    Returns None if product_id is missing or not found for this client."""
    if not product_id:
        return None
    try:
        pid = product_id if isinstance(product_id, uuid.UUID) else uuid.UUID(str(product_id))
    except (TypeError, ValueError):
        return None
    row = await session.execute(
        select(Product.price).where(
            Product.id == pid,
            Product.client_id == client_id,
        )
    )
    return row.scalar_one_or_none()


async def _merge_profile(
    session: AsyncSession,
    client_user_id: uuid.UUID,
    extracted_context: dict,
    payment_just_confirmed: bool,
    conversation_id: Optional[uuid.UUID] = None,
    product_price: Optional[object] = None,
) -> None:
    """Merge stable customer facts from extracted_context into client_users.profile.

    On payment_confirmation, also increments purchase_count and appends a
    purchase record (date, product_id, quantity, total, conversation_id) per
    the migration-008 profile contract.
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
        purchases.append(
            _build_purchase_record(
                extracted_context,
                product_price,
                conversation_id,
                datetime.now(timezone.utc),
            )
        )
        profile["purchases"] = purchases

    await session.execute(
        update(ClientUser)
        .where(ClientUser.id == client_user_id)
        .values(profile=profile)
    )


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
