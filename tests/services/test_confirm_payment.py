"""Tests for the operator confirm-payment decision (ADR-009).

Pure — evaluate_confirmation only, no DB. The DB-touching path (profile
merge, lifecycle bump, audit) reuses agent_action helpers already covered
there; the full transaction belongs to integration tests (known debt #1).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

import pytest
from app.services.confirm_payment import (
    ALREADY_CONFIRMED,
    CONFIRM_OK,
    ORDER_NOT_CONFIRMED,
    evaluate_confirmation,
)
from app.services.state_machine import InvalidTransitionError, UnknownStateError

# A context as the real first sale left it: order confirmed, payment pending.
CONFIRMED_ORDER_CTX = {
    "product_id": "abc",
    "full_name": "Juan",
    "phone": "3001234567",
    "shipping_address": "Calle 10",
    "shipping_city": "Manizales",
    "quantity": 2,
    "user_confirmation": True,
}


def test_active_with_confirmed_order_ok():
    """The real first-sale shape: conversation still active (never escalated)."""
    assert evaluate_confirmation("active", CONFIRMED_ORDER_CTX) == CONFIRM_OK


def test_human_handoff_with_confirmed_order_ok():
    assert evaluate_confirmation("human_handoff", CONFIRMED_ORDER_CTX) == CONFIRM_OK


def test_closed_and_paid_is_idempotent():
    """Double-tap / Telegram retry: no error, no duplicate purchase."""
    ctx = {**CONFIRMED_ORDER_CTX, "payment_confirmation": True}
    assert evaluate_confirmation("closed", ctx) == ALREADY_CONFIRMED


def test_active_without_user_confirmation_refused():
    """Strict precondition: nothing to close if the customer never confirmed."""
    ctx = {k: v for k, v in CONFIRMED_ORDER_CTX.items() if k != "user_confirmation"}
    assert evaluate_confirmation("active", ctx) == ORDER_NOT_CONFIRMED


def test_empty_context_refused():
    """The bot-loop handoff case: escalated conversation with empty context
    must not be confirmable as a sale."""
    assert evaluate_confirmation("human_handoff", {}) == ORDER_NOT_CONFIRMED


def test_closed_without_payment_cannot_be_reconfirmed():
    """A conversation closed by another path is terminal — closed→closed is
    not a valid transition, so the operator gets an explicit error."""
    with pytest.raises(InvalidTransitionError):
        evaluate_confirmation("closed", CONFIRMED_ORDER_CTX)


def test_unknown_state_raises():
    with pytest.raises(UnknownStateError):
        evaluate_confirmation("selling", CONFIRMED_ORDER_CTX)
