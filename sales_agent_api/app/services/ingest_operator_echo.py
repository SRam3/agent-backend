"""Operator-echo ingestion service (ADR-013, P29 fase 2).

When the human operator writes to a customer from the business's own WhatsApp
number, Meta delivers a webhook with ``changes[0].field = "smb_message_echoes"``
and the message inside ``message_echoes[]``. Until P29 that payload died in two
n8n nodes that only know how to read ``messages[]``, and the system stayed blind
to half of its own conversations.

**A echo is not a turn.** That sentence is the whole design. This module does
five things and cannot do a sixth:

  1. Validate the client.
  2. Idempotency check on the echo's wamid.
  3. Resolve the client_user (BSUID-first, same helper as the ingest).
  4. Attach to the customer's LAST conversation, whatever its state.
  5. Persist the message with ``author='operator'`` + audit event.

What is deliberately absent, and must stay absent: no debounce, no ``sleep(5)``,
no strategy directive, no ``strategy_version`` bump, no merge into
``extracted_context``, no sync to ``profile``, no state transition, no LLM call.
The invariant "no checkpoint can ever be written from an echo" holds because
this module has no code that could write one — the same shape of guarantee that
``OPERATOR_ONLY_FIELDS`` gave the payment in P11. It is not a rule in a comment;
it is an absence of capability.

PII: the echo's body is never logged. The wamid and the length, never the text.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditLog, Client, Conversation, Message
from app.services.ingest import (
    ClientNotFoundError,
    DuplicateMessageError,
    IngestError,
    _find_last_conversation,
    _mask_identity,
    _resolve_client_user,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClientNotFoundError",
    "DuplicateMessageError",
    "IngestError",
    "ingest_operator_echo",
]


async def ingest_operator_echo(
    session: AsyncSession,
    client_id: uuid.UUID,
    chakra_message_id: str,
    content: str,
    bsuid: Optional[str] = None,
    phone_number: Optional[str] = None,
    display_name: Optional[str] = None,
    message_type: str = "text",
    timestamp: Optional[datetime] = None,
) -> dict:
    """Persist one operator echo. Returns the ids; raises for expected failures.

    Raises ClientNotFoundError for an unknown/inactive tenant and
    DuplicateMessageError when Meta redelivers an echo we already stored.
    """

    # --- 1. Validate client ---------------------------------------------------
    client_row = await session.execute(
        select(Client).where(Client.id == client_id, Client.is_active.is_(True))
    )
    if client_row.scalar_one_or_none() is None:
        raise ClientNotFoundError(f"Client {client_id} not found or inactive")

    # --- 2. Idempotency check -------------------------------------------------
    # Free, courtesy of the UNIQUE index on messages.chakra_message_id (ADR-001).
    # This pre-check catches the ordinary redelivery; the IntegrityError below
    # catches the racing one. A redelivery from Meta is not an error, so the
    # endpoint turns both into 200 duplicate.
    dup_row = await session.execute(
        select(Message.id).where(Message.chakra_message_id == chakra_message_id)
    )
    if dup_row.scalar_one_or_none() is not None:
        logger.info("Duplicate operator echo rejected: wamid=%s", chakra_message_id)
        raise DuplicateMessageError(f"Echo {chakra_message_id} already processed")

    # --- 3. Resolve client_user (BSUID-first) --------------------------------
    # Same helper as the inbound ingest, on purpose: the echo carries
    # `to_user_id`, which is the same BSUID namespace as an inbound
    # `from_user_id`. The operator may also have written FIRST, to someone we
    # have never seen, so this can legitimately create the row.
    now = datetime.now(timezone.utc)
    client_user = await _resolve_client_user(
        session=session,
        client_id=client_id,
        bsuid=bsuid,
        phone_number=phone_number,
        display_name=display_name,
        now=now,
    )

    # NOTE: no block check. `is_blocked` decides whether the BOT answers a
    # customer; it has no business silencing what the human already sent.

    # --- 4. Attach to the customer's last conversation ------------------------
    # Deliberately NOT the ingest's 24h-window lookup (ADR-013 §3). Two
    # differences, both load-bearing:
    #
    #   * State is not filtered. The post-sale case is exactly the one that
    #     justified P29 over P31: the operator coordinates delivery an hour
    #     after `closed`. Requiring `state != 'closed'` would spawn a second
    #     conversation for that message and split the very dialogue we are
    #     trying to make whole.
    #   * No lazy compaction and no seed from profile. A message from the
    #     operator must never cost an LLM call.
    conversation = await _find_last_conversation(session, client_id, client_user.id)

    if conversation is None:
        # The operator wrote first, to a customer with no history at all. A bare
        # conversation — no seed, no compaction — because messages.conversation_id
        # is NOT NULL and this echo has to live somewhere.
        conversation = Conversation(
            client_id=client_id,
            client_user_id=client_user.id,
            state="active",
            extracted_context={},
            strategy_version=0,
        )
        session.add(conversation)
        await session.flush()  # get the generated id

    # --- 5. Persist the echo --------------------------------------------------
    # `direction` stays 'outbound': from the business, this message went out.
    # `author` is what was missing. And the timestamp is META's, not now() —
    # ADR-011 §5.2.4: inbound rows carry Meta's clock, so an echo stamped with
    # wall time would interleave wrongly in `recent_messages` and the LLM would
    # read the conversation out of order.
    msg_timestamp = timestamp or now
    message = Message(
        conversation_id=conversation.id,
        client_id=client_id,
        direction="outbound",
        author="operator",
        message_type=message_type,
        content=content,
        chakra_message_id=chakra_message_id,
        created_at=msg_timestamp,
    )
    session.add(message)

    # --- 6. Conversation counters --------------------------------------------
    # `last_message_at` extends the 24h session window, which is what we want
    # while a human is in the chat: the customer's reply must land in THIS
    # conversation, not a fresh one. GREATEST guards against a late redelivery
    # dragging the timestamp backwards.
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            message_count=Conversation.message_count + 1,
            last_message_at=func.greatest(Conversation.last_message_at, msg_timestamp),
        )
    )

    # Flush BEFORE the audit event: `entity_id` needs the generated message id,
    # and reading `message.id` off an un-flushed instance yields None. The
    # inbound ingest has the same ordering for the same reason.
    try:
        await session.flush()
    except IntegrityError as exc:
        # The racing redelivery: another request inserted the same wamid between
        # our pre-check and this flush. Same outcome, not an error.
        if not _is_duplicate_wamid(exc):
            raise
        logger.info(
            "Duplicate operator echo lost the insert race: wamid=%s", chakra_message_id
        )
        raise DuplicateMessageError(
            f"Echo {chakra_message_id} already processed"
        ) from exc

    # --- 7. Audit -------------------------------------------------------------
    # Length, never the body (ADR-013, postura de producción).
    session.add(
        AuditLog(
            client_id=client_id,
            event_type="operator_echo_ingested",
            entity_type="message",
            entity_id=message.id,
            actor_type="operator",
            new_value={
                "chakra_message_id": chakra_message_id,
                "identity": _mask_identity(bsuid, phone_number),
                "conversation_id": str(conversation.id),
                "conversation_state": conversation.state,
                "content_length": len(content or ""),
            },
        )
    )
    await session.flush()

    logger.info(
        "Operator echo ingested: wamid=%s conversation=%s chars=%d",
        chakra_message_id,
        conversation.id,
        len(content or ""),
    )

    return {
        "status": "ingested",
        "conversation_id": conversation.id,
        "message_id": message.id,
    }


def _is_duplicate_wamid(exc: IntegrityError) -> bool:
    """True when the IntegrityError is the chakra_message_id UNIQUE violation.

    Any other integrity error (a bad FK, a CHECK) is a real bug and must keep
    propagating as a 500 rather than being reported to n8n as a duplicate.
    """
    return "chakra_message_id" in str(getattr(exc, "orig", exc))
