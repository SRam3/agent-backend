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
  9. Compute GoalStrategyEngine directive
  10. Persist strategy state + return context
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
from app.services.prompt_context import format_business_context, format_conversation_summary

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
# Main service function
# ---------------------------------------------------------------------------
async def ingest_message(
    session: AsyncSession,
    client_id: uuid.UUID,
    chakra_message_id: str,
    phone_number: str,
    content: str,
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

    # --- 3. Upsert client_user -----------------------------------------------
    now = datetime.now(timezone.utc)
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
            set_={
                "display_name": display_name,
                "last_contact_at": now,
            },
        )
        .returning(ClientUser)
    )
    result = await session.execute(upsert_stmt)
    client_user: ClientUser = result.scalar_one()

    # --- 4. Block check -------------------------------------------------------
    if client_user.is_blocked:
        raise UserBlockedError(f"User {phone_number} is blocked")

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

    # --- 8b. Rapid-fire debounce (rearmable) ---------------------------------
    # Goal: only the LAST inbound in a burst gets to respond. We poll every
    # POLL_INTERVAL seconds; if a newer inbound has arrived we bail (that
    # one will respond instead). If no newer inbound has arrived AND enough
    # silence has accumulated since the latest inbound (which is us), we
    # proceed. A MAX_WAIT cap prevents indefinite blocking when the customer
    # keeps typing.
    #
    # Rationale (vs the previous fixed asyncio.sleep(5)): the old logic
    # measured silence from MY message timestamp, so two messages 5s apart
    # both passed and both responded — the bug observed in conversation
    # 3c618dca on May 2 (foto duplicada) and listed as deuda #2 in CLAUDE.md.
    await session.flush()
    await session.commit()

    debounce_decision = await _wait_for_silence(
        session=session,
        conversation_id=conversation.id,
        my_msg_timestamp=msg_timestamp,
    )
    if debounce_decision == "bail":
        logger.info(
            "Debounce: newer message arrived, skipping response for %s",
            chakra_message_id,
        )
        return {"should_respond": False, "reason": "debounce"}

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
                "phone_number": _mask_phone(phone_number),
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
            "profile": client_user.profile or {},
            "is_blocked": client_user.is_blocked,
        },
        "product_catalog": product_catalog,
        "business_context": format_business_context(business_rules, product_catalog),
        "conversation_summary": format_conversation_summary(
            user_context={
                "display_name": client_user.display_name,
                "profile": client_user.profile or {},
            },
            extracted_context=collected_data,
        ),
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


def _mask_phone(phone: str) -> str:
    """Mask PII: keep only last 4 digits."""
    if len(phone) <= 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]


# ---------------------------------------------------------------------------
# Rearmable debounce (paso 8b)
# ---------------------------------------------------------------------------
# Tunables. Keep MAX_WAIT comfortably under n8n's HTTP timeout (5 min default).
DEBOUNCE_POLL_INTERVAL = 2.0     # seconds between polls
DEBOUNCE_SILENCE_REQUIRED = 5.0  # seconds of silence to proceed
DEBOUNCE_MAX_WAIT = 15.0         # cap total wait per ingest


def evaluate_debounce_state(
    my_msg_timestamp: datetime,
    latest_inbound_ts: Optional[datetime],
    now: datetime,
    silence_required_seconds: float = DEBOUNCE_SILENCE_REQUIRED,
) -> str:
    """Pure decision function for one debounce poll cycle.

    Returns one of:
      - "proceed": this ingest should respond (silence achieved)
      - "bail":    a newer inbound arrived; this ingest should debounce
      - "wait":    keep polling

    The caller controls the actual sleeping and the MAX_WAIT cap.
    """
    if latest_inbound_ts is not None and latest_inbound_ts > my_msg_timestamp:
        return "bail"
    reference = latest_inbound_ts or my_msg_timestamp
    elapsed = (now - reference).total_seconds()
    if elapsed >= silence_required_seconds:
        return "proceed"
    return "wait"


async def _wait_for_silence(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    my_msg_timestamp: datetime,
) -> str:
    """Poll until silence is achieved or a newer inbound arrives.

    Returns "proceed" or "bail". Hits "proceed" automatically after
    DEBOUNCE_MAX_WAIT seconds even if the customer is still typing —
    the alternative is unbounded blocking.
    """
    loop = asyncio.get_event_loop()
    started_at = loop.time()
    while True:
        await asyncio.sleep(DEBOUNCE_POLL_INTERVAL)

        latest_row = await session.execute(
            select(func.max(Message.created_at)).where(
                Message.conversation_id == conversation_id,
                Message.direction == "inbound",
            )
        )
        latest_ts: Optional[datetime] = latest_row.scalar_one_or_none()

        decision = evaluate_debounce_state(
            my_msg_timestamp=my_msg_timestamp,
            latest_inbound_ts=latest_ts,
            now=datetime.now(timezone.utc),
        )
        if decision == "proceed":
            return "proceed"
        if decision == "bail":
            return "bail"

        # Cap total wait so an actively-typing customer eventually gets a reply.
        if loop.time() - started_at >= DEBOUNCE_MAX_WAIT:
            logger.warning(
                "Debounce: hit MAX_WAIT (%.0fs) for conversation %s; proceeding anyway",
                DEBOUNCE_MAX_WAIT, conversation_id,
            )
            return "proceed"
