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

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update, text
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
from app.services.goal_strategy import GoalStrategyEngine
from app.services.prompt_context import format_business_context, format_conversation_summary
from app.services.state_machine import get_available_actions

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
    media_url: Optional[str] = None,
    timestamp: Optional[datetime] = None,
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
        conversation = Conversation(
            client_id=client_id,
            client_user_id=client_user.id,
            state="active",
            extracted_context={},
            strategy_version=0,
        )
        session.add(conversation)
        await session.flush()  # get the generated id
    else:
        # Reset extracted_context if conversation has been idle for 30+ minutes
        # to prevent stale data from a previous interaction polluting the new one
        idle_minutes = (now - (conversation.last_message_at or conversation.created_at)).total_seconds() / 60
        if idle_minutes >= 30 and conversation.extracted_context:
            logger.info(
                "Resetting extracted_context for conversation %s (idle %.0f min)",
                conversation.id, idle_minutes,
            )
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(extracted_context={}, state="active")
            )
            conversation.extracted_context = {}
            conversation.state = "active"

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
        media_url=media_url,
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
        "available_actions": get_available_actions(conversation.state),
        "client_config": {
            "system_prompt_template": client.system_prompt_template or "",
            "ai_model": client.ai_model,
            "ai_temperature": float(client.ai_temperature),
            "business_rules": business_rules,
        },
        "user_context": {
            "display_name": client_user.display_name,
            "phone_number": _mask_phone(phone_number),
            "has_full_name": bool(client_user.full_name),
            "has_email": bool(client_user.email),
            "has_address": bool(client_user.address),
            "has_city": bool(client_user.city),
            "is_blocked": client_user.is_blocked,
        },
        "product_catalog": product_catalog,
        "business_context": format_business_context(business_rules, product_catalog),
        "conversation_summary": format_conversation_summary(
            user_context={
                "display_name": client_user.display_name,
                "has_full_name": bool(client_user.full_name),
                "has_email": bool(client_user.email),
                "has_address": bool(client_user.address),
                "has_city": bool(client_user.city),
            },
            extracted_context=collected_data,
        ),
        "recent_messages": recent_messages,
    }


def _mask_phone(phone: str) -> str:
    """Mask PII: keep only last 4 digits."""
    if len(phone) <= 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]
