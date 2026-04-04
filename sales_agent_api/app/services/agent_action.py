"""Agent action validation and execution service.

After the LLM produces a response, n8n calls this service to:
  1. Validate the proposed_action against the state machine
  2. Execute valid business actions (create_lead, propose_order, etc.)
  3. Validate and apply proposed state transitions
  4. Persist the outbound message with AI metadata
  5. Write audit log entries
  6. Return the final response to send to WhatsApp
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import (
    AuditLog,
    ClientUser,
    Conversation,
    Lead,
    Message,
    Order,
    OrderLineItem,
    Product,
)
from app.services.state_machine import (
    InvalidActionError,
    InvalidTransitionError,
    validate_action,
    validate_transition,
)

logger = logging.getLogger(__name__)

# Actions that don't produce side effects — they pass through without a handler
INFORMATIONAL_ACTIONS = {
    "ask_question",
    "search_products",
    "greet",
    "classify_intent",
    "present_product",
    "collect_shipping_info",
    "notify_human",
    "modify_order",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class AgentActionError(Exception):
    pass


class ConversationNotFoundError(AgentActionError):
    pass


class StaleContextError(AgentActionError):
    pass


# ---------------------------------------------------------------------------
# Main service function
# ---------------------------------------------------------------------------
async def process_agent_action(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    strategy_version: int,
    response_text: str,
    proposed_action: Optional[str] = None,
    proposed_transition: Optional[str] = None,
    extracted_data: Optional[dict] = None,
    ai_model: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> dict:
    """Validate and execute an agent proposal.

    Returns dict with: approved, final_response_text, new_state, side_effects, rejection_reason.
    """
    extracted_data = extracted_data or {}
    side_effects: list[str] = []
    approved = True
    rejection_reason: Optional[str] = None
    backend_decision_reason: Optional[str] = None
    action_approved: Optional[bool] = None
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

    current_state = conversation.state

    # --- 3. Validate + execute proposed_action --------------------------------
    if proposed_action and proposed_action not in INFORMATIONAL_ACTIONS:
        try:
            validate_action(current_state, proposed_action)
        except InvalidActionError as exc:
            approved = False
            rejection_reason = str(exc)
            backend_decision_reason = f"action_rejected: {exc}"
            action_approved = False
            logger.warning("Action rejected: %s", exc)
        else:
            action_approved = True
            result = await _execute_action(
                session=session,
                client_id=client_id,
                conversation=conversation,
                action=proposed_action,
                extracted_data=extracted_data,
                side_effects=side_effects,
                now=now,
            )
            if result.get("rejected"):
                approved = False
                rejection_reason = result["reason"]
                backend_decision_reason = f"business_rule_violation: {result['reason']}"
                action_approved = False
            else:
                backend_decision_reason = f"action_approved: {proposed_action}"
    elif proposed_action in INFORMATIONAL_ACTIONS:
        action_approved = True
        backend_decision_reason = f"informational_action: {proposed_action} (pass-through)"

    # --- 4. Validate + apply proposed_transition -----------------------------
    if proposed_transition and proposed_transition != current_state:
        try:
            validate_transition(current_state, proposed_transition)
        except InvalidTransitionError as exc:
            logger.warning("Transition rejected: %s", exc)
            side_effects.append(f"transition_rejected:{current_state}→{proposed_transition}")
        else:
            old_state = current_state
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(
                    state=proposed_transition,
                    previous_state=old_state,
                    agent_turn_count=Conversation.agent_turn_count + 1,
                )
            )
            conversation.state = proposed_transition
            side_effects.append(f"state_changed:{old_state}→{proposed_transition}")

            # Audit the state change
            session.add(
                AuditLog(
                    client_id=client_id,
                    event_type="state_transition",
                    entity_type="conversation",
                    entity_id=conversation.id,
                    actor_type="agent",
                    old_value={"state": old_state},
                    new_value={"state": proposed_transition},
                )
            )
    else:
        # Still count the agent turn
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(agent_turn_count=Conversation.agent_turn_count + 1)
        )

    # --- 5. Persist outbound message -----------------------------------------
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
        proposed_action=proposed_action,
        action_approved=action_approved,
        proposed_action_payload=extracted_data if proposed_action else None,
        extracted_data=extracted_data,
        backend_decision_reason=backend_decision_reason,
    )
    session.add(out_message)
    # Flush first to populate out_message.id before writing audit log
    await session.flush()

    # Audit the agent turn
    session.add(
        AuditLog(
            client_id=client_id,
            event_type="agent_turn",
            entity_type="message",
            entity_id=out_message.id,
            actor_type="agent",
            new_value={
                "proposed_action": proposed_action,
                "action_approved": action_approved,
                "side_effects": side_effects,
            },
        )
    )

    await session.flush()

    return {
        "approved": approved,
        "final_response_text": response_text,
        "new_state": conversation.state,
        "side_effects": side_effects,
        "rejection_reason": rejection_reason,
    }


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------
async def _execute_action(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    action: str,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    """Execute a business action. Returns {} on success or {"rejected": True, "reason": ...}."""

    if action == "create_lead":
        return await _handle_create_lead(
            session, client_id, conversation, extracted_data, side_effects, now
        )
    if action == "update_lead_data":
        return await _handle_update_lead_data(
            session, client_id, conversation, extracted_data, side_effects
        )
    if action == "propose_order":
        return await _handle_propose_order(
            session, client_id, conversation, extracted_data, side_effects, now
        )
    if action == "confirm_order":
        return await _handle_confirm_order(
            session, client_id, conversation, extracted_data, side_effects, now
        )
    if action == "cancel_order":
        return await _handle_cancel_order(
            session, client_id, conversation, extracted_data, side_effects, now
        )
    if action == "escalate":
        return await _handle_escalate(
            session, conversation, extracted_data, side_effects, now
        )

    # Unknown non-informational action — reject
    return {"rejected": True, "reason": f"No handler for action '{action}'"}


async def _handle_create_lead(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    intent = extracted_data.get("intent")
    if not intent:
        return {"rejected": True, "reason": "create_lead requires 'intent' in extracted_data"}

    # Reject if lead already linked to conversation
    if conversation.lead_id is not None:
        return {
            "rejected": True,
            "reason": "Conversation already has a linked lead. Use update_lead_data instead.",
        }

    lead = Lead(
        client_id=client_id,
        client_user_id=conversation.client_user_id,
        status="new",
        intent=intent,
        qualification_data=extracted_data,
        source_conversation_id=conversation.id,
    )
    session.add(lead)
    await session.flush()

    # Link lead to conversation
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(lead_id=lead.id)
    )
    conversation.lead_id = lead.id

    session.add(
        AuditLog(
            client_id=client_id,
            event_type="lead_created",
            entity_type="lead",
            entity_id=lead.id,
            actor_type="agent",
            new_value={"status": "new", "intent": intent},
        )
    )
    side_effects.append(f"lead_created:{lead.id}")
    return {}


async def _handle_update_lead_data(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
) -> dict:
    if conversation.lead_id is None:
        return {"rejected": True, "reason": "No lead linked to conversation"}

    lead_row = await session.execute(
        select(Lead).where(Lead.id == conversation.lead_id, Lead.client_id == client_id)
    )
    lead: Optional[Lead] = lead_row.scalar_one_or_none()
    if lead is None:
        return {"rejected": True, "reason": "Lead not found"}

    old_data = dict(lead.qualification_data or {})
    merged = {**old_data, **extracted_data}

    await session.execute(
        update(Lead)
        .where(Lead.id == lead.id)
        .values(qualification_data=merged)
    )

    side_effects.append(f"lead_updated:{lead.id}")
    return {}


async def _handle_propose_order(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    items = extracted_data.get("items", [])
    if not items:
        return {"rejected": True, "reason": "propose_order requires 'items' in extracted_data"}

    order = Order(
        client_id=client_id,
        client_user_id=conversation.client_user_id,
        lead_id=conversation.lead_id,
        status="draft",
        source_conversation_id=conversation.id,
    )
    session.add(order)
    await session.flush()

    subtotal = Decimal("0")
    valid_items = 0

    for item in items:
        product_id = item.get("product_id")
        quantity = max(1, int(item.get("quantity", 1)))

        if not product_id:
            continue

        prod_row = await session.execute(
            select(Product).where(
                Product.id == uuid.UUID(str(product_id)),
                Product.client_id == client_id,
                Product.is_available.is_(True),
            )
        )
        product: Optional[Product] = prod_row.scalar_one_or_none()
        if product is None:
            logger.warning("Product %s not found or unavailable — skipping line item", product_id)
            continue

        line_subtotal = product.price * quantity
        line_item = OrderLineItem(
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,  # price from catalog, never from agent
            quantity=quantity,
            subtotal=line_subtotal,
        )
        session.add(line_item)
        subtotal += line_subtotal
        valid_items += 1

    if valid_items == 0:
        # Roll back: delete the order shell
        await session.delete(order)
        return {"rejected": True, "reason": "No valid products in order"}

    await session.execute(
        update(Order)
        .where(Order.id == order.id)
        .values(subtotal=subtotal, total=subtotal)
    )

    # Link order to conversation
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(order_id=order.id)
    )
    conversation.order_id = order.id

    # Also update extracted_context with the order_id so strategy engine sees it
    new_context = {**(conversation.extracted_context or {}), "order_id": str(order.id)}
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(extracted_context=new_context)
    )

    session.add(
        AuditLog(
            client_id=client_id,
            event_type="order_created",
            entity_type="order",
            entity_id=order.id,
            actor_type="agent",
            new_value={"status": "draft", "subtotal": str(subtotal), "items": valid_items},
        )
    )
    side_effects.append(f"order_created:{order.id}")
    return {}


async def _handle_confirm_order(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    if conversation.order_id is None:
        return {"rejected": True, "reason": "No order linked to conversation"}

    order_row = await session.execute(
        select(Order).where(Order.id == conversation.order_id, Order.client_id == client_id)
    )
    order: Optional[Order] = order_row.scalar_one_or_none()
    if order is None:
        return {"rejected": True, "reason": "Order not found"}

    # Validate confirmation requirements
    missing = []
    if not order.shipping_name and not extracted_data.get("shipping_name"):
        missing.append("shipping_name")
    if not order.shipping_address and not extracted_data.get("shipping_address"):
        missing.append("shipping_address")
    if not extracted_data.get("user_confirmation"):
        missing.append("user_confirmation")

    # Check line items
    items_row = await session.execute(
        select(OrderLineItem).where(OrderLineItem.order_id == order.id)
    )
    if not items_row.scalars().all():
        missing.append("order_line_items")

    if missing:
        return {
            "rejected": True,
            "reason": f"Cannot confirm order. Missing: {', '.join(missing)}",
        }

    updates: dict = {"status": "confirmed", "confirmed_at": now}
    if extracted_data.get("shipping_name"):
        updates["shipping_name"] = extracted_data["shipping_name"]
    if extracted_data.get("shipping_address"):
        updates["shipping_address"] = extracted_data["shipping_address"]
    if extracted_data.get("shipping_city"):
        updates["shipping_city"] = extracted_data["shipping_city"]
    if extracted_data.get("shipping_phone"):
        updates["shipping_phone"] = extracted_data["shipping_phone"]

    await session.execute(update(Order).where(Order.id == order.id).values(**updates))

    session.add(
        AuditLog(
            client_id=client_id,
            event_type="order_confirmed",
            entity_type="order",
            entity_id=order.id,
            actor_type="agent",
            old_value={"status": "draft"},
            new_value={"status": "confirmed"},
        )
    )
    side_effects.append(f"order_confirmed:{order.id}")
    return {}


async def _handle_cancel_order(
    session: AsyncSession,
    client_id: uuid.UUID,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    if conversation.order_id is None:
        return {"rejected": True, "reason": "No order linked to conversation"}

    order_row = await session.execute(
        select(Order).where(Order.id == conversation.order_id, Order.client_id == client_id)
    )
    order: Optional[Order] = order_row.scalar_one_or_none()
    if order is None:
        return {"rejected": True, "reason": "Order not found"}

    if order.status not in ("draft", "confirmed"):
        return {
            "rejected": True,
            "reason": f"Cannot cancel order in status '{order.status}'",
        }

    cancel_reason = extracted_data.get("cancel_reason", "Cancelled by agent")
    await session.execute(
        update(Order)
        .where(Order.id == order.id)
        .values(status="cancelled", cancelled_at=now, cancel_reason=cancel_reason)
    )

    session.add(
        AuditLog(
            client_id=client_id,
            event_type="order_cancelled",
            entity_type="order",
            entity_id=order.id,
            actor_type="agent",
            old_value={"status": order.status},
            new_value={"status": "cancelled", "cancel_reason": cancel_reason},
        )
    )
    side_effects.append(f"order_cancelled:{order.id}")
    return {}


async def _handle_escalate(
    session: AsyncSession,
    conversation: Conversation,
    extracted_data: dict,
    side_effects: list[str],
    now: datetime,
) -> dict:
    reason = extracted_data.get("escalation_reason", "Escalated by agent")
    old_state = conversation.state

    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            state="human_handoff",
            previous_state=old_state,
            escalation_reason=reason,
        )
    )
    conversation.state = "human_handoff"

    session.add(
        AuditLog(
            client_id=conversation.client_id,
            event_type="escalated",
            entity_type="conversation",
            entity_id=conversation.id,
            actor_type="agent",
            old_value={"state": old_state},
            new_value={"state": "human_handoff", "reason": reason},
        )
    )
    side_effects.append(f"escalated:{reason[:50]}")
    return {}
