"""Tests for the agent_action service — pure-python only.

Covers the persistence DECISION (which extracted_data fields get merged into
extracted_context, and the purchase record shape), without a live DB. The
session-touching parts of process_agent_action belong to integration tests
(pending — see CLAUDE.md deuda #1).

Focus:
  - ORDER_FIELDS (quantity/grind/roast) persist alongside STRATEGY_FIELDS.
  - ORDER_FIELDS are NOT strategy/DAG fields → never bump lifecycle/checkpoints.
  - The user_confirmation DAG gate still rejects when its prerequisites are
    missing (regression guard for P2).
  - payment_confirmation is OPERATOR-ONLY: an agent turn never accepts it, never
    records a sale, never promotes to 'customer' (ADR-009 / fix venta duplicada).
  - The purchase record carries quantity + total per the migration-008 contract.
"""
import sys
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.agent_action import (
    OPERATOR_ONLY_FIELDS,
    ORDER_FIELDS,
    STRATEGY_FIELDS,
    compute_context_updates,
    is_new_user_confirmation,
    snapshot_language,
    trigger_message_at,
    _build_purchase_record,
    _coerce_int,
)
from sqlalchemy.sql.dml import Update

from app.services.order_summary import (
    FIXED,
    NO_SUMMARY,
    ORDER_MODIFIED_THIS_TURN,
    Shipping,
    SummaryState,
    compute_fingerprint,
)

# --- ADR-010 fixtures -------------------------------------------------------
# The gate now needs backend-owned facts: which summary we last presented, when,
# and which inbound triggered this turn. These build a conversation in which a
# summary IS standing and current, so a confirmation can legitimately pass.
MANIZALES = Shipping(cost=Decimal("5000"), status=FIXED)
SENT_AT = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)
AFTER = SENT_AT + timedelta(seconds=20)


def summarisable_context(**overrides) -> dict:
    ctx = {
        "product_id": "1f1f1f1f-0000-0000-0000-000000000001",
        "quantity": 2,
        "grind_preference": "grano",
        "full_name": "Ana Ruiz",
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
        "shipping_city": "Manizales",
    }
    ctx.update(overrides)
    return ctx


def standing_summary(context: dict) -> SummaryState:
    return SummaryState(
        fingerprint=compute_fingerprint(context, MANIZALES), sent_at=SENT_AT
    )


def confirmable(context: dict) -> dict:
    """The kwargs that make a user_confirmation acceptable for `context`."""
    return {
        "summary_state": standing_summary(context),
        "trigger_message_at": AFTER,
        "shipping": MANIZALES,
    }


# ---------------------------------------------------------------------------
# ORDER_FIELDS / STRATEGY_FIELDS invariants
# ---------------------------------------------------------------------------
def test_order_fields_are_disjoint_from_strategy_fields():
    """The whole point: order details must NOT become DAG checkpoints."""
    assert ORDER_FIELDS.isdisjoint(STRATEGY_FIELDS)
    assert ORDER_FIELDS == {"quantity", "grind_preference", "roast_preference"}


def test_payment_is_the_only_operator_only_field():
    """payment_confirmation stays a DAG checkpoint (the engine reads it from
    extracted_context) but no agent turn may write it — only the operator
    endpoint does (ADR-009)."""
    assert OPERATOR_ONLY_FIELDS == {"payment_confirmation"}
    assert OPERATOR_ONLY_FIELDS <= STRATEGY_FIELDS


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
            "gate": "data",
            "missing": ["full_name", "phone", "shipping_address", "shipping_city"],
        }
    ]


def test_user_confirmation_accepted_when_complete():
    """Sufficient data AND a standing, current summary the customer answered
    after seeing it. This is the 2026-07-15 shape, the only legitimate
    confirmation in the whole history."""
    ctx = summarisable_context()
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, ctx, **confirmable(ctx)
    )
    assert accepted.get("user_confirmation") == "sí"
    assert strategy_accepted.get("user_confirmation") == "sí"
    assert rejections == []


def test_sufficient_data_alone_no_longer_confirms():
    """THE regression guard of ADR-010. Data sufficiency was never the question:
    it is exactly what let "Barrio El Campin" close a sale on 2026-08-19. With
    all four slots present but no summary ever presented, the confirmation is
    refused."""
    accepted, _, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, summarisable_context()
    )
    assert "user_confirmation" not in accepted
    assert rejections == [
        {"field": "user_confirmation", "gate": "context", "missing": [NO_SUMMARY]}
    ]


def test_confirmation_riding_on_an_order_change_is_refused():
    """The 2026-07-20 shape: "Unidad campestre sorry" is a correction, not a
    confirmation. The turn moves the order out from under the summary."""
    ctx = summarisable_context()
    accepted, _, rejections = compute_context_updates(
        {"user_confirmation": "sí", "shipping_address": "Unidad campestre"},
        ctx,
        **confirmable(ctx),
    )
    assert "user_confirmation" not in accepted
    assert accepted["shipping_address"] == "Unidad campestre"
    assert ORDER_MODIFIED_THIS_TURN in rejections[0]["missing"]


# ---------------------------------------------------------------------------
# Invalidation (ADR-010 §6)
# ---------------------------------------------------------------------------
def test_a_modification_invalidates_a_standing_confirmation():
    """The path that fixes the "impossible to unmark" defect. The customer had
    confirmed; then they changed the quantity. The confirmation they gave was for
    the previous order, so the backend clears it."""
    ctx = summarisable_context(user_confirmation=True)
    accepted, strategy_accepted, _ = compute_context_updates({"quantity": 5}, ctx)
    assert accepted["user_confirmation"] is False
    assert strategy_accepted["user_confirmation"] is False


def test_invalidation_writes_false_instead_of_deleting_the_key():
    """False, not a missing key: every reader uses .get() truthiness
    (confirm_payment.py:78, goal_strategy, prompt_context), and False keeps the
    trace that a confirmation once stood."""
    ctx = summarisable_context(user_confirmation=True)
    accepted, _, _ = compute_context_updates({"quantity": 5}, ctx)
    assert "user_confirmation" in accepted
    assert accepted["user_confirmation"] is False


def test_reconfirming_after_a_correction_is_a_new_transition():
    """So the operator gets a Telegram notice for the CORRECTED order, not one
    stale notice for the order the customer already changed."""
    invalidated = summarisable_context(quantity=5, user_confirmation=False)
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, invalidated, **confirmable(invalidated)
    )
    assert rejections == []
    assert is_new_user_confirmation(strategy_accepted, invalidated) is True


def test_an_unchanged_order_does_not_invalidate():
    """The LLM re-proposes the whole cumulative extracted_data every turn. A
    re-proposal of the same values is not a modification."""
    ctx = summarisable_context(user_confirmation=True)
    accepted, _, _ = compute_context_updates(
        {"quantity": "2", "full_name": "Ana Ruiz"}, ctx
    )
    assert accepted.get("user_confirmation") is not False


def test_llm_cannot_unmark_a_confirmation_itself():
    """No-regression guard: the truthy filter of compute_context_updates is NOT
    relaxed by ADR-010. Unmarking is the backend's authority, on deterministic
    evidence that the order moved — never the model's assertion."""
    ctx = summarisable_context(user_confirmation=True)
    accepted, strategy_accepted, _ = compute_context_updates(
        {"user_confirmation": False}, ctx
    )
    assert "user_confirmation" not in accepted
    assert "user_confirmation" not in strategy_accepted


# ---------------------------------------------------------------------------
# trigger_message_at / snapshot_language (read back from strategy_snapshot)
# ---------------------------------------------------------------------------
def test_trigger_message_at_reads_the_snapshot():
    assert trigger_message_at({"trigger_message_at": "2026-08-19T15:00:20+00:00"}) == AFTER


def test_trigger_message_at_fails_closed():
    """Missing or unparseable ⇒ None ⇒ condition 3 refuses. A refusal costs a
    turn of friction; a wrong acceptance records a sale."""
    assert trigger_message_at(None) is None
    assert trigger_message_at({}) is None
    assert trigger_message_at({"trigger_message_at": "no es una fecha"}) is None


def test_naive_trigger_timestamp_is_treated_as_utc():
    assert trigger_message_at({"trigger_message_at": "2026-08-19T15:00:20"}) == AFTER


def test_snapshot_language_defaults_to_spanish():
    assert snapshot_language({"live_language": "en"}) == "en"
    assert snapshot_language({"live_language": "es"}) == "es"
    assert snapshot_language({}) == "es"
    assert snapshot_language(None) == "es"


def test_payment_confirmation_rejected_when_incomplete():
    accepted, _, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, {}
    )
    assert "payment_confirmation" not in accepted
    assert rejections == [
        {
            "field": "payment_confirmation",
            "gate": "operator",
            "missing": ["operator_confirmation"],
        }
    ]


def test_payment_confirmation_rejected_even_with_every_prereq_present():
    """THE invariant of the duplicate-sale fix: prerequisites are irrelevant.
    The LLM has no authority over a payment, so a perfectly-formed proposal is
    dropped exactly like an incomplete one — 'ya pagué' is not proof."""
    ctx = {
        "user_confirmation": "sí",
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
    }
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert "payment_confirmation" not in accepted
    assert "payment_confirmation" not in strategy_accepted
    assert rejections == [
        {
            "field": "payment_confirmation",
            "gate": "operator",
            "missing": ["operator_confirmation"],
        }
    ]


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
# P3 (superseded) — the payment gate is gone: payment is rejected UNCONDITIONALLY,
# so P3's scenario (payment riding on a same-turn user_confirmation) can no longer
# exist. These keep the P3 scenarios as regression cases anyway: the guarantee
# they encoded still holds, now for a stronger reason.
# ---------------------------------------------------------------------------
def test_payment_does_not_sneak_through_when_user_confirmation_rejected_same_turn():
    """The P3 bug: in ONE turn the LLM sends user_confirmation=true AND
    payment_confirmation=true, but full_name is missing. user_confirmation is
    rejected for the missing name; payment is rejected for having been proposed
    at all."""
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
    payment_rej = next(r for r in rejections if r["field"] == "payment_confirmation")
    assert payment_rej["missing"] == ["operator_confirmation"]


def test_payment_rejected_when_user_confirmation_came_from_prior_turn():
    """The legitimate-looking case that used to record the sale: order confirmed
    in a PREVIOUS turn, customer now says they paid. Still rejected — the sale is
    recorded by the operator endpoint or not at all."""
    ctx = {
        "user_confirmation": "sí",  # accepted in a prior turn
        "phone": "3001234567",
        "shipping_address": "Cra 1 # 2-3",
    }
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"payment_confirmation": "comprobante.jpg"}, ctx
    )
    assert "payment_confirmation" not in accepted
    assert "payment_confirmation" not in strategy_accepted
    assert rejections == [
        {
            "field": "payment_confirmation",
            "gate": "operator",
            "missing": ["operator_confirmation"],
        }
    ]


def test_valid_user_confirmation_survives_alongside_a_rejected_payment():
    """A payment proposal must not poison the rest of the turn: a
    user_confirmation with every prerequisite present is still accepted."""
    ctx = summarisable_context()
    accepted, strategy_accepted, rejections = compute_context_updates(
        {"user_confirmation": "sí", "payment_confirmation": "comprobante.jpg"},
        ctx,
        **confirmable(ctx),
    )
    assert accepted.get("user_confirmation") == "sí"
    assert strategy_accepted.get("user_confirmation") == "sí"
    assert "payment_confirmation" not in accepted
    assert rejections == [
        {
            "field": "payment_confirmation",
            "gate": "operator",
            "missing": ["operator_confirmation"],
        }
    ]


def test_order_fields_persist_even_when_confirmation_and_payment_rejected():
    """P2 regression: order details persist regardless of the user_confirmation
    gate and the payment drop firing in the same turn."""
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
    assert rejections == [
        {"field": "phone", "gate": "format", "missing": ["plausible_format"]}
    ]


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
    assert rejections == [
        {"field": "phone", "gate": "format", "missing": ["plausible_format"]}
    ]


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
    ctx = summarisable_context(phone="basura-previa")
    accepted, _, rejections = compute_context_updates(
        {"user_confirmation": "sí"}, ctx, **confirmable(ctx)
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


def test_the_breaker_does_not_count_the_operators_messages():
    """The breaker asks "is the BOT repeating itself?", so it must only see the
    bot's own rows.

    Since ADR-013 an operator echo is an outbound row too, and one of them
    landing between two identical bot replies would push the second out of this
    two-row window and hide the loop — a silent regression in P8 introduced by
    a front that has nothing to do with it. IS DISTINCT FROM rather than !=, so
    the pre-migration-015 rows whose author is still NULL keep counting.
    """
    client_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    conv_id = uuid.UUID("00000000-0000-0000-0000-000000000009")
    sql = str(_recent_outbound_stmt(client_id, conv_id))
    assert "messages.author IS DISTINCT FROM" in sql


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


def _make_conversation(state="active", extracted_context=None, summary_state=None,
                      strategy_snapshot=None):
    state_ = summary_state or SummaryState(fingerprint=None, sent_at=None)
    return SimpleNamespace(
        id=_CONV_ID,
        client_id=_CLIENT_ID,
        client_user_id=uuid.uuid4(),
        state=state,
        strategy_version=3,
        extracted_context=extracted_context if extracted_context is not None else {},
        active_goal="close_sale",
        # ADR-010 (migration 013)
        strategy_snapshot=strategy_snapshot,
        order_summary_fingerprint=state_.fingerprint,
        order_summary_sent_at=state_.sent_at,
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


# ---------------------------------------------------------------------------
# is_new_user_confirmation — pre-payment notice trigger (ADR-009 §2)
# ---------------------------------------------------------------------------

def test_new_user_confirmation_fires_on_transition():
    from app.services.agent_action import is_new_user_confirmation
    assert is_new_user_confirmation({"user_confirmation": True}, {}) is True


def test_new_user_confirmation_silent_when_already_confirmed():
    """The LLM re-proposes cumulative data every turn — no re-notification."""
    from app.services.agent_action import is_new_user_confirmation
    assert is_new_user_confirmation(
        {"user_confirmation": True}, {"user_confirmation": True}
    ) is False


def test_new_user_confirmation_silent_without_proposal():
    from app.services.agent_action import is_new_user_confirmation
    assert is_new_user_confirmation({"full_name": "Juan"}, {}) is False


# ---------------------------------------------------------------------------
# Fix venta duplicada — the LLM never closes a sale (on the REAL turn path)
# ---------------------------------------------------------------------------
import app.services.agent_action as agent_action_module

# The context as the 20-jul e2e left it right before the bug: order confirmed
# in a previous turn, everything collected, payment still pending.
_CONFIRMED_ORDER_CTX = {
    "product_id": "p-uuid",
    "full_name": "Ana Ruiz",
    "phone": "3001234567",
    "shipping_address": "Cra 1 # 2-3",
    "shipping_city": "Manizales",
    "quantity": 2,
    "user_confirmation": "sí",
}


def _record_profile_calls(monkeypatch) -> dict:
    """Replace the two profile writers with recorders. Asserting on their
    arguments is the point: they are the ONLY way a sale reaches the profile."""
    calls: dict = {}

    async def fake_merge(session, **kwargs):
        calls["merge"] = kwargs

    async def fake_bump(session, **kwargs):
        calls["bump"] = kwargs

    monkeypatch.setattr(agent_action_module, "_merge_profile", fake_merge)
    monkeypatch.setattr(agent_action_module, "_bump_lifecycle_stage", fake_bump)
    return calls


def test_llm_payment_claim_records_no_sale_and_moves_nothing(monkeypatch):
    """The duplicate-sale bug, replayed on the real path: the customer writes
    'ya pagué' and the LLM proposes payment_confirmation with every prerequisite
    already in context. No sale in the profile, no promotion to 'customer', no
    payment in the context, no escalation — only a visible warning."""
    conversation = _make_conversation(state="active")
    conversation.extracted_context = dict(_CONFIRMED_ORDER_CTX)
    calls = _record_profile_calls(monkeypatch)
    session = _StubSession(
        [
            _StubResult(scalar=conversation),                    # load conversation
            _StubResult(scalars_list=["B", "A"]),                # previous outbounds
            _StubResult(),                                       # UPDATE extracted_context
            _StubResult(scalar=SimpleNamespace(business_rules={})),  # client
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text="Recibido, muchas gracias.",
            # the LLM re-proposes the cumulative data every turn, payment included
            extracted_data={**_CONFIRMED_ORDER_CTX, "payment_confirmation": "ya pagué"},
        )
    )

    # the claim is dropped but never silent
    assert "warning:payment_claim_ignored" in result["side_effects"]
    assert "payment_confirmation" not in conversation.extracted_context

    # no sale recorded, no lifecycle promotion
    assert calls["merge"]["payment_just_confirmed"] is False
    assert calls["bump"]["target"] == "engaged"

    # no lifecycle move for the conversation either, and the turn still flows
    assert conversation.state == "active"
    assert result["new_state"] == "active"
    assert not any(s.startswith("escalated:") for s in result["side_effects"])
    assert result["approved"] is True


def test_payment_claim_alone_writes_nothing(monkeypatch):
    """When the payment claim is the only thing proposed, nothing is persisted:
    no context UPDATE, no profile write — just the warning."""
    conversation = _make_conversation(state="active")
    conversation.extracted_context = dict(_CONFIRMED_ORDER_CTX)
    calls = _record_profile_calls(monkeypatch)
    session = _StubSession(
        [
            _StubResult(scalar=conversation),
            _StubResult(scalars_list=["B", "A"]),
            _StubResult(scalar=SimpleNamespace(business_rules={})),  # client
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text="Recibido, muchas gracias.",
            extracted_data={"payment_confirmation": True},
        )
    )

    assert result["side_effects"] == ["warning:payment_claim_ignored"]
    assert conversation.extracted_context == _CONFIRMED_ORDER_CTX
    assert calls == {}


def test_closed_conversation_is_never_resurrected_by_auto_escalate():
    """A turn already in flight when the operator closes the sale still passes the
    staleness check (confirm_payment does not bump strategy_version) and sees the
    operator's payment_confirmation → all_complete. It must NOT drag the closed
    conversation back to human_handoff, where ingest's 24h window would trap the
    customer's next message in a conversation the bot must not answer."""
    conversation = _make_conversation(state="closed")
    conversation.extracted_context = {
        **_CONFIRMED_ORDER_CTX,
        "payment_confirmation": True,  # written by the operator endpoint
    }
    session = _StubSession(
        [
            _StubResult(scalar=conversation),
            _StubResult(scalars_list=["B", "A"]),
            _StubResult(scalar=SimpleNamespace(business_rules={})),
        ]
    )

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text="Recibido, muchas gracias.",
        )
    )

    assert conversation.state == "closed"
    assert result["new_state"] == "closed"
    assert not any(s.startswith("escalated:") for s in result["side_effects"])
    # The auto-escalate block was never entered: no UPDATE of any kind was
    # issued. (The client lookup is no longer a proxy for that — since ADR-010
    # it happens on every turn, to resolve the shipping rules.)
    assert not any(isinstance(stmt, Update) for stmt in session.executed)


# ---------------------------------------------------------------------------
# The backend renders and sends the summary (ADR-010 §1) — through the real
# process_agent_action path, with a stub session.
# ---------------------------------------------------------------------------
_ARENILLO_RULES = {
    "shipping_rules": {"cities": {"Manizales": {"cost": 5000}}},
    "presentation": {"es": "bolsas de 340g", "es_singular": "bolsa de 340g"},
}
_PRODUCT_UUID = "1f1f1f1f-0000-0000-0000-000000000001"


def _summary_session(conversation, price=Decimal("40000"), profile_merge=False):
    """conversation → previous outbounds → client → [profile lookups] → price.

    `profile_merge` covers the turns where something is actually accepted: the
    context UPDATE plus the two client_user lookups (profile merge + lifecycle
    bump) all consume from the queue before the price fetch.
    """
    results = [
        _StubResult(scalar=conversation),
        _StubResult(scalars_list=[]),
        _StubResult(scalar=SimpleNamespace(business_rules=_ARENILLO_RULES)),
    ]
    if profile_merge:
        results += [_StubResult(), _StubResult(), _StubResult()]
    results.append(_StubResult(scalar=price))
    return _StubSession(results)


def _run(session, response_text="Claro, ya te confirmo.", extracted_data=None):
    return asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=3,
            response_text=response_text,
            extracted_data=extracted_data,
        )
    )


def test_backend_summary_replaces_the_llm_text():
    """The replacement IS the fix: it turns "the summary was sent" into a fact
    the backend owns, and takes the money arithmetic away from the model."""
    conversation = _make_conversation(
        extracted_context=summarisable_context(product_id=_PRODUCT_UUID)
    )
    session = _summary_session(conversation)

    result = _run(session, response_text="El total es como $90.000 más o menos.")

    assert result["final_response_text"] == (
        "Va el pedido entonces: 2 bolsas de 340g en grano para Ana Ruiz, "
        "al 3001234567, en Manizales, Cra 1 # 2-3. "
        "El café son $80.000 y el envío $5.000, total $85.000. "
        "¿Todo bien con esos datos?"
    )
    assert "order_summary_sent" in result["side_effects"]
    assert conversation.order_summary_fingerprint is not None
    assert conversation.order_summary_sent_at is not None


def test_the_persisted_outbound_is_the_summary_not_the_discarded_llm_text():
    """The messages trail must record what the customer actually received —
    otherwise the next turn's history lies to the LLM, and so does the audit."""
    conversation = _make_conversation(
        extracted_context=summarisable_context(product_id=_PRODUCT_UUID)
    )
    session = _summary_session(conversation)

    result = _run(session, response_text="El total es como $90.000 más o menos.")

    outbound = next(o for o in session.added if isinstance(o, Message))
    assert outbound.content == result["final_response_text"]
    assert "$90.000" not in outbound.content


def test_no_summary_without_a_price_so_none_without_a_product():
    """H6, structurally: no price without product_id, no summary without price,
    no confirmation without summary. The LLM's text goes out untouched."""
    conversation = _make_conversation(
        extracted_context=summarisable_context(product_id=None)
    )
    session = _summary_session(conversation, price=None)

    result = _run(session, response_text="¿Cuál te interesa?")

    assert result["final_response_text"] == "¿Cuál te interesa?"
    assert not any(s.startswith("order_summary") for s in result["side_effects"])
    assert conversation.order_summary_fingerprint is None


def test_the_same_summary_is_never_sent_twice():
    """Idempotent by construction — which is what lets the circuit breaker stay
    where it is, comparing the LLM's text before the merge."""
    context = summarisable_context(product_id=_PRODUCT_UUID)
    conversation = _make_conversation(
        extracted_context=context, summary_state=standing_summary(context)
    )
    session = _summary_session(conversation)

    result = _run(session, response_text="Quedo atento entonces.")

    assert result["final_response_text"] == "Quedo atento entonces."
    assert not any(s.startswith("order_summary") for s in result["side_effects"])


def test_a_correction_invalidates_and_re_summarises_in_the_same_turn():
    """The §6 cycle end to end: the customer had confirmed, then changed the
    quantity. The confirmation is cleared and a fresh summary goes out — a sale
    is never closed on a summary that no longer reflects what was ordered."""
    context = summarisable_context(product_id=_PRODUCT_UUID)
    conversation = _make_conversation(
        extracted_context={**context, "user_confirmation": True},
        summary_state=standing_summary(context),
    )
    session = _summary_session(conversation, profile_merge=True)

    result = _run(session, extracted_data={"quantity": 4})

    assert conversation.extracted_context["user_confirmation"] is False
    assert "user_confirmation_invalidated:quantity" in result["side_effects"]
    assert "order_summary_resent" in result["side_effects"]
    assert "4 bolsas de 340g" in result["final_response_text"]
    assert "total $165.000" in result["final_response_text"]


def test_a_rejected_confirmation_is_visible_per_condition():
    """One side effect per failed condition: in production these are the only
    instrument for knowing which of the four is doing the work."""
    context = summarisable_context(product_id=_PRODUCT_UUID)
    conversation = _make_conversation(extracted_context=context)
    session = _summary_session(conversation)

    result = _run(session, extracted_data={"user_confirmation": "sí"})

    assert f"warning:confirmation_rejected_{NO_SUMMARY}" in result["side_effects"]
    assert conversation.extracted_context.get("user_confirmation") is None


# ---------------------------------------------------------------------------
# INV-CONV-003 — a turn computed on an old state is never applied (ADR-003)
#
# Each turn is two HTTP calls with the LLM in between. The strategy_version the
# ingest hands out is the only thing that tells /agent/action whether the world
# moved in the meantime. Until these tests, nothing in the suite exercised it.
# ---------------------------------------------------------------------------
import pytest

from app.services.agent_action import StaleContextError


def test_a_matching_strategy_version_applies_the_turn():
    """The ordinary turn: same version as the conversation → persisted."""
    conversation = _make_conversation()
    session = _summary_session(conversation)

    result = _run(session, response_text="Hola, ¿qué café te gustaría?")

    assert result["approved"] is True
    assert any(isinstance(obj, Message) for obj in session.added)


def test_an_older_strategy_version_is_rejected_as_stale():
    """Another ingest bumped the version while the LLM was thinking: the turn
    was decided on a world that no longer exists, and nothing of it is written."""
    conversation = _make_conversation(extracted_context={"full_name": "Ana Ruiz"})
    session = _summary_session(conversation)

    with pytest.raises(StaleContextError):
        asyncio.run(
            process_agent_action(
                session=session,
                client_id=_CLIENT_ID,
                conversation_id=_CONV_ID,
                strategy_version=2,
                response_text="Listo, ya quedó tu pedido.",
                extracted_data={"quantity": 5},
            )
        )

    assert session.added == []
    assert len(session.executed) == 1  # only the conversation load
    assert conversation.extracted_context == {"full_name": "Ana Ruiz"}
    assert conversation.state == "active"
    assert conversation.strategy_version == 3


def test_a_newer_strategy_version_is_also_stale():
    """The guard is equality, not "not older": a version the conversation never
    issued is just as untrustworthy as an old one."""
    session = _summary_session(_make_conversation())

    with pytest.raises(StaleContextError):
        asyncio.run(
            process_agent_action(
                session=session,
                client_id=_CLIENT_ID,
                conversation_id=_CONV_ID,
                strategy_version=4,
                response_text="Hola",
            )
        )

    assert session.added == []


def test_an_echo_in_flight_leaves_the_turn_applicable():
    """Coherence with INV-OP-004: an operator echo that lands between the two
    calls does NOT bump the version, so the in-flight turn is still applied.
    That is exactly the uncovered half of INV-OP-005 (P34) — if an echo ever
    starts bumping, this test is the one that has to change on purpose."""
    from tests.services.test_ingest_operator_echo import (
        FakeSession as EchoSession,
        _conversation as echo_conversation,
        _results as echo_results,
        _run as run_echo,
    )

    handed_out = 23  # what the ingest returned to n8n before the LLM call
    echoed = echo_conversation(strategy_version=handed_out)
    run_echo(EchoSession(echo_results(echoed)))

    conversation = _make_conversation()
    conversation.strategy_version = echoed.strategy_version
    session = _summary_session(conversation)

    result = asyncio.run(
        process_agent_action(
            session=session,
            client_id=_CLIENT_ID,
            conversation_id=_CONV_ID,
            strategy_version=handed_out,
            response_text="Hola, ¿qué café te gustaría?",
        )
    )

    assert result["approved"] is True
