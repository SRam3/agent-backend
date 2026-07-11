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
# ADR-008 — phone gate: implausible phones never persist
# ---------------------------------------------------------------------------
def test_implausible_phone_rejected():
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"phone": "hola"}, {}
    )
    assert "phone" not in accepted
    assert "phone" not in strategy_accepted
    assert rejections == [{"field": "phone", "missing": ["plausible_format"]}]


def test_implausible_phone_leaves_rest_of_turn_intact():
    """Un phone basura no debe frenar el resto del turno: los demás campos
    persisten como siempre."""
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"phone": "123", "full_name": "Ana Ruiz", "quantity": 2}, {}
    )
    assert "phone" not in accepted
    assert accepted.get("full_name") == "Ana Ruiz"
    assert accepted.get("quantity") == 2
    assert strategy_accepted.get("full_name") == "Ana Ruiz"
    assert rejections == [{"field": "phone", "missing": ["plausible_format"]}]


def test_rejected_phone_does_not_satisfy_user_confirmation_gate():
    """Un phone rechazado ESTE turno no puede contar como prerequisito de
    user_confirmation en el mismo turno."""
    accepted, _, rejections = compute_context_updates(
        {
            "phone": "abc",
            "user_confirmation": "sí",
            "full_name": "Ana Ruiz",
            "shipping_address": "Cra 1 # 2-3",
            "shipping_city": "Manizales",
        },
        {},
    )
    assert "phone" not in accepted
    assert "user_confirmation" not in accepted
    rejected_fields = {r["field"] for r in rejections}
    assert rejected_fields == {"phone", "user_confirmation"}
    phone_rej = next(r for r in rejections if r["field"] == "user_confirmation")
    assert "phone" in phone_rej["missing"]


def test_valid_phone_still_persists():
    """Regresión: un phone plausible sigue persistiendo como hoy."""
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"phone": "+57 300 123 4567"}, {}
    )
    assert accepted.get("phone") == "+57 300 123 4567"
    assert strategy_accepted.get("phone") == "+57 300 123 4567"
    assert rejections == []


def test_stand_case_phone_passes_the_gate():
    """El número del stand (14 dígitos) cabe en E.164 y pasa — deliberado."""
    accepted, _, rejections = compute_context_updates(
        {"phone": "31071484777779"}, {}
    )
    assert accepted.get("phone") == "31071484777779"
    assert rejections == []


def test_phone_already_in_context_is_not_regated():
    """El gate valida el phone PROPUESTO este turno; uno ya persistido en
    extracted_context no se toca."""
    accepted, _, rejections = compute_context_updates(
        {"user_confirmation": "sí"},
        {
            "phone": "basura-previa",
            "full_name": "Ana Ruiz",
            "shipping_address": "Cra 1 # 2-3",
            "shipping_city": "Manizales",
        },
    )
    assert accepted.get("user_confirmation") == "sí"
    assert rejections == []


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


# ---------------------------------------------------------------------------
# P8 — circuit breaker: 3rd consecutive identical outbound
# ---------------------------------------------------------------------------
import asyncio
from types import SimpleNamespace

from app.models.core import AuditLog, Message
from app.services.agent_action import (
    LOOP_SIDE_EFFECT,
    _recent_outbound_stmt,
    detect_outbound_loop,
    process_agent_action,
)

_SAME = "Lo siento, pero aquí solo hablamos de café. ¿Te interesa algo del menú?"


def test_loop_fires_on_third_identical():
    """2 previous identical outbounds + the same candidate = 3rd → fires."""
    assert detect_outbound_loop(_SAME, [_SAME, _SAME]) is True


def test_loop_does_not_fire_on_second_identical():
    """Only 1 previous identical: it's the 2nd, not the 3rd → no fire."""
    assert detect_outbound_loop(_SAME, [_SAME]) is False


def test_loop_does_not_fire_with_no_history():
    assert detect_outbound_loop(_SAME, []) is False


def test_loop_does_not_fire_on_non_consecutive_repeat():
    """A, B, A: with B in between, the two most recent are (B, A) → no fire."""
    assert detect_outbound_loop("A", ["B", "A"]) is False


def test_loop_does_not_fire_on_different_texts():
    """3 consecutive but different texts → exact comparison never fires."""
    assert detect_outbound_loop("C", ["B", "A"]) is False


def test_loop_comparison_is_exact_not_fuzzy():
    """A single-character difference is a different response by design."""
    assert detect_outbound_loop(_SAME, [_SAME, _SAME + " "]) is False


def test_recent_outbound_stmt_is_tenant_safe_ordered_and_limited():
    """The previous-outbounds read must filter by client_id AND conversation_id
    AND direction='outbound', newest-first, limit 2 — tenant isolation is not
    optional. Asserted on the compiled statement, no DB needed."""
    client_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    conv_id = uuid.UUID("00000000-0000-0000-0000-000000000009")
    sql = str(_recent_outbound_stmt(client_id, conv_id))
    assert "messages.client_id" in sql
    assert "messages.conversation_id" in sql
    assert "messages.direction" in sql
    assert "ORDER BY messages.created_at DESC" in sql
    assert "LIMIT" in sql


# --- stub session: realistic process_agent_action paths without a DB --------
class _StubResult:
    def __init__(self, scalar=None, scalars_list=None):
        self._scalar = scalar
        self._scalars_list = scalars_list or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._scalars_list


class _StubSession:
    """Answers execute() from an ordered queue and records every statement,
    so tests can assert WHAT was executed on the real code path."""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []
        self.added = []

    async def execute(self, stmt, params=None):
        self.executed.append(stmt)
        if self._results:
            return self._results.pop(0)
        return _StubResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


_CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _make_conversation(state="active"):
    return SimpleNamespace(
        id=_CONV_ID,
        client_id=_CLIENT_ID,
        client_user_id=uuid.uuid4(),
        state=state,
        strategy_version=3,
        extracted_context={},
        active_goal="close_sale",
    )


def test_breaker_fires_escalates_and_suppresses_response():
    """Fire path end-to-end: 2 identical previous outbounds + same text →
    human_handoff, circuit_breaker side_effect, approved=False, empty
    final_response_text, and NO outbound Message persisted (only AuditLog)."""
    conversation = _make_conversation(state="active")
    session = _StubSession(
        [
            _StubResult(scalar=conversation),           # load conversation
            _StubResult(scalars_list=[_SAME, _SAME]),   # 2 previous outbounds
            # state UPDATE needs no result
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text=_SAME,
        )
    )

    assert result["approved"] is False
    assert result["final_response_text"] == ""
    assert result["new_state"] == "human_handoff"
    assert result["side_effects"] == [LOOP_SIDE_EFFECT]
    assert result["rejection_reason"] == "loop_detected"
    assert conversation.state == "human_handoff"

    # the previous-outbounds read on the real path is exactly the tenant-safe
    # builder statement (client_id + conversation_id + direction filters)
    assert str(session.executed[1]) == str(_recent_outbound_stmt(_CLIENT_ID, _CONV_ID))

    # the 3rd identical outbound is NOT persisted; the audit trail is
    assert not any(isinstance(obj, Message) for obj in session.added)
    audit = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit) == 1
    assert audit[0].event_type == "circuit_breaker"
    assert audit[0].new_value["reason"] == "loop_detected"
    # the audit payload carries the count, never the response content
    assert _SAME not in str(audit[0].new_value)


def test_breaker_suppresses_without_transition_when_already_handed_off():
    """Already in human_handoff (n8n doesn't cut on state yet): the identical
    response is still suppressed, but no new transition is attempted."""
    conversation = _make_conversation(state="human_handoff")
    session = _StubSession(
        [
            _StubResult(scalar=conversation),
            _StubResult(scalars_list=[_SAME, _SAME]),
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text=_SAME,
        )
    )

    assert result["approved"] is False
    assert result["new_state"] == "human_handoff"
    assert result["side_effects"] == [LOOP_SIDE_EFFECT]
    # only the 2 selects ran — no state UPDATE was issued
    assert len(session.executed) == 2
    assert not any(isinstance(obj, Message) for obj in session.added)


def test_no_fire_normal_flow_persists_outbound():
    """Regression: non-consecutive repeat (B, A then A again) does NOT fire —
    the turn flows normally, approved=True, outbound persisted."""
    conversation = _make_conversation(state="active")
    client = SimpleNamespace(business_rules={})
    session = _StubSession(
        [
            _StubResult(scalar=conversation),            # load conversation
            _StubResult(scalars_list=["B", "A"]),        # previous outbounds
            _StubResult(scalar=client),                  # client for auto-escalate
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text="A",
        )
    )

    assert result["approved"] is True
    assert result["final_response_text"] == "A"
    assert result["new_state"] == "active"
    assert LOOP_SIDE_EFFECT not in result["side_effects"]

    outbound = [obj for obj in session.added if isinstance(obj, Message)]
    assert len(outbound) == 1
    assert outbound[0].direction == "outbound"
    assert outbound[0].content == "A"
