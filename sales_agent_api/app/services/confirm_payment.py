"""Operator payment confirmation — closes the handoff loop (ADR-009).

`payment_confirmed` is the one checkpoint whose truth lives outside the whole
system: a human looking at a payment receipt. The LLM must never propose it
("ya pagué" is not proof) and the backend cannot verify it (it never sees the
image). This service is the return channel: an authorized operator — via
whatever skin (Telegram today) — asserts "the payment is real", and the system
records the sale, closes the conversation and updates the customer profile.

The conversation may be in `active` (the first real sale never escalated) or
`human_handoff`; both transition to `closed` here. `closed` ends the SALE, not
the relationship — a returning customer gets a fresh conversation via ingest.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditLog, Conversation
from app.services.agent_action import (
    _bump_lifecycle_stage,
    _fetch_product_price,
    _merge_profile,
)
from app.services.state_machine import validate_transition

logger = logging.getLogger(__name__)


class ConfirmPaymentError(Exception):
    pass


class ConversationNotFoundError(ConfirmPaymentError):
    pass


class OrderNotConfirmedError(ConfirmPaymentError):
    """The customer never confirmed the order (no user_confirmation in context) —
    there is nothing to close. Protects against confirming the wrong conversation."""
    pass


# Pure decision outcomes — kept as constants so the endpoint and tests share them.
CONFIRM_OK = "ok"
ALREADY_CONFIRMED = "already_confirmed"
ORDER_NOT_CONFIRMED = "order_not_confirmed"


def evaluate_confirmation(state: str, extracted_context: dict) -> str:
    """Decide what an operator confirmation means for this conversation.

    Pure — no I/O. Returns one of:
      - ALREADY_CONFIRMED: sale already closed by a prior confirmation —
        idempotent no-op (safe for double-taps / Telegram retries).
      - ORDER_NOT_CONFIRMED: no user_confirmation in context — refuse.
      - CONFIRM_OK: proceed.

    Raises StateMachineError for states that cannot transition to closed
    (e.g. a conversation closed WITHOUT payment — not re-confirmable).
    """
    if state == "closed" and extracted_context.get("payment_confirmation"):
        return ALREADY_CONFIRMED
    if not extracted_context.get("user_confirmation"):
        return ORDER_NOT_CONFIRMED
    validate_transition(state, "closed")
    return CONFIRM_OK


async def confirm_payment(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> dict:
    """Mark a sale as paid on behalf of a human operator.

    In one transaction (caller commits):
      1. payment_confirmation → extracted_context
      2. sale recorded in client_users.profile (purchases[], purchase_count —
         P2 shape) + lifecycle bump to 'customer'
      3. conversation → closed
      4. audit event `sale_closed` (actor_type='operator')

    Returns dict with: already_confirmed, new_state, side_effects.
    """
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

    context = dict(conversation.extracted_context or {})
    decision = evaluate_confirmation(conversation.state, context)

    if decision == ALREADY_CONFIRMED:
        return {
            "already_confirmed": True,
            "new_state": conversation.state,
            "side_effects": [],
        }

    if decision == ORDER_NOT_CONFIRMED:
        raise OrderNotConfirmedError(
            f"Conversation {conversation_id} has no user_confirmation — "
            "the customer never confirmed an order; nothing to close"
        )

    old_state = conversation.state
    context["payment_confirmation"] = True
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(extracted_context=context, state="closed")
    )
    conversation.extracted_context = context
    conversation.state = "closed"

    product_price = await _fetch_product_price(
        session, client_id, context.get("product_id")
    )
    await _merge_profile(
        session,
        client_user_id=conversation.client_user_id,
        extracted_context=context,
        payment_just_confirmed=True,
        conversation_id=conversation.id,
        product_price=product_price,
    )
    await _bump_lifecycle_stage(session, conversation.client_user_id, "customer")

    side_effects = [
        "payment_confirmed_by_operator",
        f"state_changed:{old_state}→closed",
        "sale_recorded_in_profile",
    ]
    session.add(
        AuditLog(
            client_id=client_id,
            event_type="sale_closed",
            entity_type="conversation",
            entity_id=conversation.id,
            actor_type="operator",
            new_value={
                "old_state": old_state,
                "new_state": "closed",
                "side_effects": side_effects,
            },
        )
    )
    await session.flush()

    logger.info(
        "Sale closed by operator: conversation=%s old_state=%s",
        conversation.id,
        old_state,
    )
    return {
        "already_confirmed": False,
        "new_state": "closed",
        "side_effects": side_effects,
    }
