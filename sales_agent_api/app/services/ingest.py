"""Message ingestion service.

Performs 10 operations in a single database transaction:
  1. Validate client
  2. Idempotency check
  3. Upsert client_user
  4. Block check
  5. Find or create conversation (24-hour session window)
  6. Acquire advisory lock
  7. Persist inbound message
  8. Update conversation counters
  8b. Persist and release the advisory lock
  8c. Suppress the turn if the message has no readable content
  8d. Rapid-fire debounce
  9. Compute GoalStrategyEngine directive
  10. Persist strategy state + return context

Steps 8c and 8d both return early via `build_suppressed_response`: a complete,
valid response that tells n8n not to answer. The message is already committed
by 8b, so the next turn still sees it in `recent_messages`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import (
    AuditLog,
    Client,
    ClientUser,
    Conversation,
    Message,
    Product,
)
from app.services.conversation_summary import (
    SummarizerLLM,
    needs_summary,
    summarize_conversation,
)
from app.services.goal_strategy import GoalStrategyEngine
from app.services.language import detect_language
from app.services.prompt_context import (
    format_business_context,
    format_conversation_summary,
    format_language_directive,
)

logger = logging.getLogger(__name__)

_engine = GoalStrategyEngine()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class IngestError(Exception):
    """Base for ingest service errors."""


class ClientNotFoundError(IngestError):
    pass


class DuplicateMessageError(IngestError):
    pass


class UserBlockedError(IngestError):
    pass


# ---------------------------------------------------------------------------
# Readable content
# ---------------------------------------------------------------------------
def is_unreadable(content: Optional[str]) -> bool:
    """True when there is nothing for the LLM to answer.

    Deliberately a question about CONTENT, not about `message_type`. Meta keeps
    inventing types — `unsupported`, `edit`, and whatever comes next — so an
    allowlist of types is a new bug per type. The right question is not "what
    kind of message is this?" but "is there anything to reply to?".

    Measured against prod on 2026-08-22: of 79 inbound that got through, 20 had
    nothing readable (16 `unsupported`, 3 `audio`, 1 `edit`) and the bot
    answered every one of them blind.
    """
    return not (content or "").strip()


#: SQL twin of :func:`is_unreadable`, for the debounce lookahead. Keep the two
#: in step: if one learns a new notion of "empty", so must the other.
def _sql_has_readable_content(column):
    return func.btrim(func.coalesce(column, "")) != ""


# ---------------------------------------------------------------------------
# Suppressed-turn response
# ---------------------------------------------------------------------------
def build_suppressed_response(
    reason: str,
    conversation: Optional[Conversation] = None,
) -> dict:
    """A complete, valid ingest response that tells n8n *not* to answer.

    Every field of IngestMessageResponse is present. That is the whole point:
    the debounce path used to return a two-key dict, which the endpoint then
    fed to IngestMessageResponse(**result) and blew up with 500 on every
    single coalescence. The path never once returned a valid response.

    `conversation_state` matters as much as `should_respond`: the n8n node
    "IF Should Respond" tests BOTH, so a missing state is not inert.

    When a conversation is available (debounce, unreadable content) the real
    identifiers go out instead of placeholders; the duplicate path has no
    conversation to report and keeps the historical dummy uuid.
    """
    return {
        "should_respond": False,
        "reason": reason,
        "conversation_id": conversation.id if conversation is not None else uuid.uuid4(),
        "conversation_state": conversation.state if conversation is not None else "active",
        "strategy_directive": "",
        "strategy_meta": {},
        "strategy_version": conversation.strategy_version if conversation is not None else 0,
        "client_config": {},
        "user_context": {},
        "product_catalog": [],
        "business_context": "",
        "conversation_summary": "",
        "recent_messages": [],
    }


# ---------------------------------------------------------------------------
# Main service function
# ---------------------------------------------------------------------------
async def ingest_message(
    session: AsyncSession,
    client_id: uuid.UUID,
    chakra_message_id: str,
    content: str,
    bsuid: Optional[str] = None,
    phone_number: Optional[str] = None,
    display_name: Optional[str] = None,
    message_type: str = "text",
    timestamp: Optional[datetime] = None,
    summarizer_llm: Optional[SummarizerLLM] = None,
) -> dict:
    """Process an inbound WhatsApp message.

    Returns a dict with all fields needed by n8n to call the LLM.
    Raises IngestError subclasses for expected failure modes.
    """

    # --- 1. Validate client ---------------------------------------------------
    client_row = await session.execute(
        select(Client).where(Client.id == client_id, Client.is_active.is_(True))
    )
    client: Optional[Client] = client_row.scalar_one_or_none()
    if client is None:
        raise ClientNotFoundError(f"Client {client_id} not found or inactive")

    # --- 2. Idempotency check -------------------------------------------------
    dup_row = await session.execute(
        select(Message.id).where(Message.chakra_message_id == chakra_message_id)
    )
    if dup_row.scalar_one_or_none() is not None:
        logger.info("Duplicate message rejected: chakra_message_id=%s", chakra_message_id)
        raise DuplicateMessageError(f"Message {chakra_message_id} already processed")

    # --- 3. Resolve client_user (BSUID-first) --------------------------------
    now = datetime.now(timezone.utc)
    client_user = await _resolve_client_user(
        session=session,
        client_id=client_id,
        bsuid=bsuid,
        phone_number=phone_number,
        display_name=display_name,
        now=now,
    )

    # --- 4. Block check -------------------------------------------------------
    if client_user.is_blocked:
        raise UserBlockedError(
            f"User {_mask_identity(bsuid, phone_number)} is blocked"
        )

    # --- 5. Find or create conversation (24h window) -------------------------
    window_start = now - timedelta(hours=24)
    conv_row = await session.execute(
        select(Conversation)
        .where(
            Conversation.client_id == client_id,
            Conversation.client_user_id == client_user.id,
            Conversation.state != "closed",
            Conversation.last_message_at >= window_start,
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )
    conversation: Optional[Conversation] = conv_row.scalar_one_or_none()

    if conversation is None:
        # Lazy compaction: if the customer has a previous conversation that
        # hasn't been summarized into their profile yet, compact it now so
        # the new conversation starts with full memory of the last one.
        # We only pay the LLM cost when the customer actually returns.
        prev_conv = await _find_last_conversation(session, client_id, client_user.id)
        enriched_profile = dict(client_user.profile or {})
        if prev_conv is not None and needs_summary(enriched_profile, prev_conv.id):
            summary = await summarize_conversation(
                session, prev_conv.id, llm=summarizer_llm
            )
            if summary is not None:
                # Mirror what _persist_to_profile wrote, so the seed below
                # and the user_context returned to n8n both reflect the
                # freshly compacted memory without needing session.refresh.
                enriched_profile["last_conversation_summary"] = summary
                if summary.get("language"):
                    enriched_profile["language"] = summary["language"]
                if summary.get("communication_style"):
                    enriched_profile["communication_style"] = summary["communication_style"]
                client_user.profile = enriched_profile

        seeded_context = _seed_context_from_profile(enriched_profile)
        conversation = Conversation(
            client_id=client_id,
            client_user_id=client_user.id,
            state="active",
            extracted_context=seeded_context,
            strategy_version=0,
        )
        session.add(conversation)
        await session.flush()  # get the generated id

    # --- 6. Advisory lock on conversation ------------------------------------
    lock_key = int(hashlib.sha1(str(conversation.id).encode()).hexdigest(), 16) % (2**63)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
    )

    # --- 7. Persist inbound message ------------------------------------------
    msg_timestamp = timestamp or now
    message = Message(
        conversation_id=conversation.id,
        client_id=client_id,
        direction="inbound",
        message_type=message_type,
        content=content,
        chakra_message_id=chakra_message_id,
        created_at=msg_timestamp,
    )
    session.add(message)

    # --- 8. Update conversation counters ------------------------------------
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            message_count=Conversation.message_count + 1,
            last_message_at=now,
        )
    )
    conversation.message_count += 1
    conversation.last_message_at = now

    # --- 8b. Persist and release the advisory lock ---------------------------
    # Flush to persist the message, then commit to release the advisory lock
    # so other messages from the same user can be inserted.
    await session.flush()
    await session.commit()

    # --- 8c. Unreadable-content guard ----------------------------------------
    # The message is persisted and committed by now, so the next turn will still
    # find it in recent_messages — that is what makes suppressing it safe. What
    # we skip is everything downstream: the 5s debounce wait, the strategy
    # computation, and (via should_respond=false) the LLM call.
    #
    # Order is not negotiable: persist, THEN suppress. A guard in n8n would
    # never have called /ingest at all and the message would vanish.
    if is_unreadable(content):
        logger.info(
            "Unreadable content: no turn for %s (message_type=%s)",
            chakra_message_id,
            message_type,
        )
        return build_suppressed_response("unreadable_content", conversation)

    # --- 8d. Rapid-fire debounce ---------------------------------------------
    # Sleep briefly, then check whether a newer inbound arrived — if so, let
    # that one respond instead of answering each burst message separately.
    await asyncio.sleep(5)

    newer_msg = await session.execute(
        select(Message.id)
        .where(
            Message.conversation_id == conversation.id,
            Message.direction == "inbound",
            Message.created_at > msg_timestamp,
            # Only defer to a message that will actually take a turn. Without
            # this, a text followed by a voice note answers nothing at all:
            # the text defers to the audio, and the audio suppresses itself.
            _sql_has_readable_content(Message.content),
        )
        .limit(1)
    )
    if newer_msg.scalar_one_or_none() is not None:
        logger.info(
            "Debounce: newer message exists, skipping response for %s",
            chakra_message_id,
        )
        return build_suppressed_response("debounce", conversation)

    # Re-acquire advisory lock for the rest of the processing
    lock_key2 = int(hashlib.sha1(str(conversation.id).encode()).hexdigest(), 16) % (2**63)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key2}
    )

    # Reload conversation state (may have changed during sleep)
    conv_row2 = await session.execute(
        select(Conversation).where(Conversation.id == conversation.id)
    )
    conversation = conv_row2.scalar_one()

    # --- 9. Compute strategy -------------------------------------------------
    business_rules: dict = client.business_rules or {}
    goal = conversation.active_goal or business_rules.get("default_goal", "close_sale")
    collected_data: dict = conversation.extracted_context or {}

    directive = _engine.compute(goal, collected_data, business_rules)

    # Live language (ADR-008). Computed here, before the snapshot is written, so
    # /agent/action can read it back: the summary the BACKEND now renders has to
    # come out in the customer's language too (ADR-010 §1), and that call never
    # sees the inbound text.
    live_language = detect_language(content)

    # --- 10. Persist strategy state ------------------------------------------
    new_strategy_version = conversation.strategy_version + 1
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            active_goal=goal,
            current_checkpoint=directive.current_checkpoint,
            progress_pct=directive.progress_pct,
            strategy_version=new_strategy_version,
            last_strategy_at=now,
            strategy_snapshot={
                "goal": directive.goal,
                "progress_pct": directive.progress_pct,
                "current_checkpoint": directive.current_checkpoint,
                "missing_fields": directive.missing_fields,
                "completed_checkpoints": directive.completed_checkpoints,
                # Which inbound produced this strategy_version (ADR-010 §5,
                # condition 3). /agent/action never receives the customer's
                # message, and asking for "the latest inbound" there is wrong:
                # the debounce commits a message at step 8b and only bumps the
                # version 5s later, so a burst leaves a newer inbound that has
                # not invalidated anything yet — and the condition would pass on
                # a message the customer had not yet been answered. Writing it
                # here ties the verdict to the message the LLM was answering,
                # with no change to n8n (strategy_version already round-trips).
                "trigger_message_at": msg_timestamp.isoformat(),
                "live_language": live_language,
            },
        )
    )
    conversation.strategy_version = new_strategy_version
    conversation.active_goal = goal

    # Audit log
    session.add(
        AuditLog(
            client_id=client_id,
            event_type="message_ingest",
            entity_type="message",
            entity_id=message.id,
            actor_type="system",
            new_value={
                "chakra_message_id": chakra_message_id,
                "identity": _mask_identity(bsuid, phone_number),
                "conversation_id": str(conversation.id),
            },
        )
    )

    await session.flush()

    # --- Load product catalog -------------------------------------------------
    products_rows = await session.execute(
        select(Product)
        .where(Product.client_id == client_id, Product.is_available.is_(True))
        .order_by(Product.name)
    )
    product_catalog = [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "sku": p.sku,
            "price": float(p.price),
            "ai_description": p.ai_description,
            "image_url": p.image_url,
        }
        for p in products_rows.scalars().all()
    ]

    # --- Build recent messages list (last 20) --------------------------------
    recent_rows = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    recent_messages = [
        {
            "id": str(m.id),
            "direction": m.direction,
            "content": m.content,
            "message_type": m.message_type,
            "created_at": m.created_at.isoformat(),
        }
        for m in reversed(recent_rows.scalars().all())
    ]

    # --- Conversation summary block -------------------------------------------
    # The language directive goes FIRST: position drives adherence (ADR-008).
    conversation_summary = (
        format_language_directive(live_language)
        + "\n\n"
        + format_conversation_summary(
            user_context={
                "display_name": client_user.display_name,
                "profile": client_user.profile or {},
            },
            extracted_context=collected_data,
            live_language=live_language,
        )
    )

    return {
        "should_respond": True,
        "conversation_id": conversation.id,
        "conversation_state": conversation.state,
        "strategy_directive": directive.to_prompt(),
        "strategy_meta": {
            "goal": directive.goal,
            "progress_pct": directive.progress_pct,
            "current_checkpoint": directive.current_checkpoint,
            "next_action": directive.next_action,
            "missing_fields": directive.missing_fields,
        },
        "strategy_version": new_strategy_version,
        "client_config": {
            "system_prompt_template": client.system_prompt_template or "",
            "ai_model": client.ai_model,
            "ai_temperature": float(client.ai_temperature),
            "business_rules": business_rules,
        },
        "user_context": {
            "display_name": client_user.display_name,
            "phone_number": _mask_phone(phone_number),
            "bsuid": _mask_phone(bsuid) if bsuid else None,
            "profile": client_user.profile or {},
            "is_blocked": client_user.is_blocked,
        },
        "product_catalog": product_catalog,
        "business_context": format_business_context(business_rules, product_catalog),
        "conversation_summary": conversation_summary,
        "recent_messages": recent_messages,
    }


def _seed_context_from_profile(profile: dict) -> dict:
    """Pull stable customer facts out of the profile into a fresh extracted_context
    so the strategy engine already sees what we know from past conversations.

    Also rehydrates the pending_intent (product/quantity the customer was
    about to buy) from the last conversation summary, so an interrupted
    sale resumes where it left off.
    """
    if not profile:
        return {}
    seed: dict = {}
    for src, dst in (
        ("full_name", "full_name"),
        ("email", "email"),
        ("shipping_address", "shipping_address"),
        ("city", "shipping_city"),
        ("phone", "phone"),
    ):
        if profile.get(src):
            seed[dst] = profile[src]

    last_summary = profile.get("last_conversation_summary") or {}
    pending = last_summary.get("pending_intent") or {}
    if pending.get("product_id"):
        seed["product_id"] = pending["product_id"]
    if pending.get("quantity"):
        seed["quantity"] = pending["quantity"]
    return seed


async def _find_last_conversation(
    session: AsyncSession,
    client_id: uuid.UUID,
    client_user_id: uuid.UUID,
) -> Optional[Conversation]:
    """Most recent conversation for this customer, regardless of state.
    Used to detect if there's a previous conversation to compact when a
    new one is about to be created."""
    row = await session.execute(
        select(Conversation)
        .where(
            Conversation.client_id == client_id,
            Conversation.client_user_id == client_user_id,
        )
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _resolve_client_user(
    session: AsyncSession,
    client_id: uuid.UUID,
    bsuid: Optional[str],
    phone_number: Optional[str],
    display_name: Optional[str],
    now: datetime,
) -> ClientUser:
    """Find or create the client_user for an inbound message, BSUID-first.

    WhatsApp's number-privacy rollout means the phone number is no longer a
    reliable identity: a customer may arrive with only a BSUID. Resolution
    order:

      1. By (client_id, bsuid) — the real identity when we have it.
      2. By (client_id, phone_number) — a customer we met BEFORE P14 is stored
         phone-keyed with bsuid NULL. Reusing that row is what keeps them from
         being duplicated and losing their profile/history. We deliberately do
         NOT write the bsuid back onto that row: proving "these two identities
         are the same person" and merging them is the identity redesign, which
         has its own ADR. Here we only avoid making the problem worse.
      3. Insert. Uses ON CONFLICT so that a concurrent ingest for the same new
         customer converges instead of raising — the advisory lock is only
         taken later (step 6), so this step is genuinely racy.

    When no bsuid is supplied at all (n8n before P14 Fase 3) this falls through
    to the original phone-keyed upsert, unchanged.
    """
    set_on_match = {"display_name": display_name, "last_contact_at": now}

    if bsuid:
        found = await session.execute(
            select(ClientUser).where(
                ClientUser.client_id == client_id, ClientUser.bsuid == bsuid
            )
        )
        existing: Optional[ClientUser] = found.scalar_one_or_none()

        if existing is None and phone_number:
            found = await session.execute(
                select(ClientUser).where(
                    ClientUser.client_id == client_id,
                    ClientUser.phone_number == phone_number,
                )
            )
            existing = found.scalar_one_or_none()

        if existing is not None:
            if display_name:
                existing.display_name = display_name
            existing.last_contact_at = now
            return existing

        # New customer. Safe against uq_client_user_phone: we only get here when
        # the phone lookup above found nothing (or there is no phone at all, and
        # NULLs do not collide in a unique index).
        insert_stmt = (
            pg_insert(ClientUser)
            .values(
                client_id=client_id,
                bsuid=bsuid,
                phone_number=phone_number,
                display_name=display_name,
                first_contact_at=now,
                last_contact_at=now,
            )
            .on_conflict_do_update(
                index_elements=["client_id", "bsuid"],
                set_=set_on_match,
            )
            .returning(ClientUser)
        )
        return (await session.execute(insert_stmt)).scalar_one()

    # No BSUID: the pre-P14 path, byte-for-byte as it was.
    upsert_stmt = (
        pg_insert(ClientUser)
        .values(
            client_id=client_id,
            phone_number=phone_number,
            display_name=display_name,
            first_contact_at=now,
            last_contact_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_client_user_phone",
            set_=set_on_match,
        )
        .returning(ClientUser)
    )
    return (await session.execute(upsert_stmt)).scalar_one()


def _mask_phone(phone: Optional[str]) -> str:
    """Mask PII: keep only last 4 digits."""
    if not phone:
        return "****"
    if len(phone) <= 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]


def _mask_identity(bsuid: Optional[str], phone_number: Optional[str]) -> str:
    """Mask whichever identity we have, preferring the BSUID.

    Never returns a bare None: a privacy-enabled customer has no phone at all,
    and log lines saying "User None" help nobody.
    """
    if bsuid:
        return _mask_phone(bsuid)
    return _mask_phone(phone_number)
