"""Tests for the agent_action service — pure-python only.

Covers the persistence DECISION (which extracted_data fields get merged into
extracted_context, and the purchase record shape), without a live DB. The
session-touching parts of process_agent_action belong to integration tests
(pending — see CLAUDE.md deuda #1).

Focus:
  - ORDER_FIELDS (quantity/grind/roast) persist alongside STRATEGY_FIELDS.
  - ORDER_FIELDS are NOT strategy/DAG fields → never bump lifecycle/checkpoints.
  - DAG gates (user_confirmation, payment_confirmation) still reject when their
    prerequisites are missing (regression guard for P2).
  - The purchase record carries quantity + total per the migration-008 contract.
"""
import sys
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.agent_action import (
    ORDER_FIELDS,
    STRATEGY_FIELDS,
    compute_context_updates,
    _build_purchase_record,
    _coerce_int,
)


# ---------------------------------------------------------------------------
# ORDER_FIELDS / STRATEGY_FIELDS invariants
# ---------------------------------------------------------------------------
def test_order_fields_are_disjoint_from_strategy_fields():
    """The whole point: order details must NOT become DAG checkpoints."""
    assert ORDER_FIELDS.isdisjoint(STRATEGY_FIELDS)
    assert ORDER_FIELDS == {"quantity", "grind_preference", "roast_preference"}


# ---------------------------------------------------------------------------
# compute_context_updates — order fields persist
# ---------------------------------------------------------------------------
def test_quantity_persists_to_context():
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"quantity": 2}, {}
    )
    assert accepted.get("quantity") == 2
    # quantity is not a DAG field, so it must not appear as a strategy update
    assert "quantity" not in strategy_accepted
    assert rejections == []


def test_grind_and_roast_persist_to_context():
    accepted, _, _ = compute_context_updates(
        {"grind_preference": "molido", "roast_preference": "medio"}, {}
    )
    assert accepted["grind_preference"] == "molido"
    assert accepted["roast_preference"] == "medio"


def test_order_fields_persist_alongside_strategy_fields():
    accepted, strategy_accepted, _ = compute_context_updates(
        {"quantity": 3, "product_id": "p-uuid"}, {}
    )
    assert accepted["quantity"] == 3
    assert accepted["product_id"] == "p-uuid"
    # only product_id is a strategy field
    assert strategy_accepted == {"product_id": "p-uuid"}


def test_falsy_order_values_are_skipped():
    accepted, _, _ = compute_context_updates(
        {"quantity": 0, "grind_preference": ""}, {}
    )
    assert "quantity" not in accepted
    assert "grind_preference" not in accepted


def test_unknown_fields_are_ignored():
    accepted, strategy_accepted, _ = compute_context_updates(
        {"send_image_url": "http://x", "favorite_color": "blue"}, {}
    )
    assert accepted == {}
    assert strategy_accepted == {}


# ---------------------------------------------------------------------------
# compute_context_updates — DAG gates (regression guard)
# ---------------------------------------------------------------------------
def test_user_confirmation_rejected_when_incomplete():
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, {}
    )
    assert "user_confirmation" not in accepted
    assert "user_confirmation" not in strategy_accepted
    assert rejections == [
        {
            "field": "user_confirmation",
            "missing": ["full_name", "phone", "shipping_address", "shipping_city"],
        }
    ]


def test_user_confirmation_accepted_when_complete():
    ctx = {
        "full_name": "Ana Ruiz",
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
        "shipping_city": "Manizales",
    }
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, ctx
    )
    assert accepted.get("user_confirmation") == "sí"
    assert strategy_accepted.get("user_confirmation") == "sí"
    assert rejections == []


def test_payment_confirmation_rejected_when_incomplete():
    accepted, _, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, {}
    )
    assert "payment_confirmation" not in accepted
    assert rejections and rejections[0]["field"] == "payment_confirmation"


def test_payment_confirmation_accepted_when_prereqs_present():
    ctx = {
        "user_confirmation": "sí",
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
    }
    accepted, _, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert accepted.get("payment_confirmation") == "comprobante.jpg"
    assert rejections == []


def test_order_field_does_not_satisfy_a_gate():
    """A quantity in the same turn must not help user_confirmation pass."""
    accepted, _, rejections = compute_context_updates(
        {"quantity": 2, "user_confirmation": "sí"}, {}
    )
    # quantity persists, confirmation still rejected
    assert accepted.get("quantity") == 2
    assert "user_confirmation" not in accepted
    assert rejections and rejections[0]["field"] == "user_confirmation"


# ---------------------------------------------------------------------------
# P3 — payment gate must not be permeable to a same-turn rejected confirmation
# ---------------------------------------------------------------------------
def test_payment_does_not_sneak_through_when_user_confirmation_rejected_same_turn():
    """The P3 bug: in ONE turn the LLM sends user_confirmation=true AND
    payment_confirmation=true, but full_name is missing. user_confirmation is
    rejected for the missing name; payment_confirmation must NOT pass on the back
    of a confirmation that did not survive this turn."""
    ctx = {
        # full_name deliberately absent
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
        "shipping_city": "Manizales",
    }
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí", "payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert "user_confirmation" not in accepted
    assert "payment_confirmation" not in accepted
    assert "payment_confirmation" not in strategy_accepted
    assert {r["field"] for r in rejections} == {
        "user_confirmation",
        "payment_confirmation",
    }
    # payment was rejected specifically because user_confirmation is now absent
    payment_rej = next(r for r in rejections if r["field"] == "payment_confirmation")
    assert "user_confirmation" in payment_rej["missing"]


def test_payment_passes_when_user_confirmation_came_from_prior_turn():
    """Regression: a user_confirmation already persisted in a PREVIOUS turn still
    satisfies payment's prerequisite. Only the same-turn rejected case is blocked —
    the legitimate payment flow must keep working."""
    ctx = {
        "user_confirmation": "sí",  # accepted in a prior turn
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
    }
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert accepted.get("payment_confirmation") == "comprobante.jpg"
    assert strategy_accepted.get("payment_confirmation") == "comprobante.jpg"
    assert rejections == []


def test_payment_passes_when_user_confirmation_valid_same_turn():
    """When user_confirmation IS valid this turn (all prereqs present) it survives
    the gate and stays in merged, so a same-turn payment_confirmation still passes.
    The P3 recompute must not strip an ACCEPTED confirmation."""
    ctx = {
        "full_name": "Ana Ruiz",
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
        "shipping_city": "Manizales",
    }
    accepted, _, rejections = compute_context_updates(
        {"user_confirmation": "sí", "payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert accepted.get("user_confirmation") == "sí"
    assert accepted.get("payment_confirmation") == "comprobante.jpg"
    assert rejections == []


def test_order_fields_persist_even_when_both_gates_reject():
    """P2 regression under the P3 change: order details persist regardless of the
    user/payment gates firing in the same turn."""
    accepted, strategy_accepted, rejections = compute_context_updates(
        {
            "quantity": 2,
            "grind_preference": "molido",
            "user_confirmation": "sí",
            "payment_confirmation": "comprobante.jpg",
        },
        {},
    )
    assert accepted["quantity"] == 2
    assert accepted["grind_preference"] == "molido"
    assert "user_confirmation" not in accepted
    assert "payment_confirmation" not in accepted
    assert strategy_accepted == {}


# ---------------------------------------------------------------------------
# _coerce_int
# ---------------------------------------------------------------------------
def test_coerce_int_handles_int_str_and_garbage():
    assert _coerce_int(2) == 2
    assert _coerce_int("3") == 3
    assert _coerce_int(" 4 ") == 4
    assert _coerce_int(None) is None
    assert _coerce_int("dos bolsas") is None


# ---------------------------------------------------------------------------
# _build_purchase_record — migration-008 contract
# ---------------------------------------------------------------------------
_CONV_ID = uuid.UUID("00000000-0000-0000-0000-000000000009")
_NOW = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)


def test_purchase_record_carries_quantity_and_total():
    record = _build_purchase_record(
        {"product_id": "p-uuid", "quantity": 2},
        Decimal("40000"),
        _CONV_ID,
        _NOW,
    )
    assert record["product_id"] == "p-uuid"
    assert record["quantity"] == 2
    assert record["total"] == 80000.0
    assert record["conversation_id"] == str(_CONV_ID)
    assert record["date"] == _NOW.isoformat()


def test_purchase_record_total_none_when_price_unknown():
    record = _build_purchase_record(
        {"product_id": "p-uuid", "quantity": 2}, None, _CONV_ID, _NOW
    )
    assert record["quantity"] == 2
    assert record["total"] is None


def test_purchase_record_quantity_none_when_absent():
    record = _build_purchase_record(
        {"product_id": "p-uuid"}, Decimal("40000"), _CONV_ID, _NOW
    )
    assert record["quantity"] is None
    assert record["total"] is None


def test_purchase_record_keys_match_contract():
    record = _build_purchase_record({}, None, _CONV_ID, _NOW)
    assert set(record.keys()) == {
        "date", "product_id", "quantity", "total", "conversation_id",
    }
