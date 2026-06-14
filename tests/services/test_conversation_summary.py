"""Tests for the conversation_summary service.

Pure Python — no database, no real OpenAI calls. The DB-touching parts of
summarize_conversation are exercised in integration tests (pending — see
CLAUDE.md deuda #1).

Here we cover:
  - SUMMARY_SCHEMA shape conforms to OpenAI strict json_schema rules
  - _build_system_prompt embeds the product catalog
  - _build_user_prompt embeds messages + extracted_context + state
  - needs_summary correctly compares conversation_id against profile
"""
import sys
import os
import asyncio
import logging
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.conversation_summary import (
    SUMMARY_SCHEMA,
    _build_system_prompt,
    _build_user_prompt,
    get_summary_failure_count,
    needs_summary,
    summarize_conversation,
)


# ---------------------------------------------------------------------------
# SUMMARY_SCHEMA shape
# ---------------------------------------------------------------------------
def test_summary_schema_is_strict_and_named():
    assert SUMMARY_SCHEMA["name"] == "conversation_summary"
    assert SUMMARY_SCHEMA["strict"] is True
    assert SUMMARY_SCHEMA["schema"]["additionalProperties"] is False


def test_summary_schema_required_fields_match_properties():
    """OpenAI strict mode requires 'required' to list every property."""
    schema = SUMMARY_SCHEMA["schema"]
    assert set(schema["required"]) == set(schema["properties"].keys())


def test_summary_schema_outcome_enum_covers_full_funnel():
    outcomes = SUMMARY_SCHEMA["schema"]["properties"]["outcome"]["enum"]
    # Every checkpoint of the close_sale DAG should be representable as an
    # abandonment point, plus the terminal outcomes.
    assert "purchased" in outcomes
    assert "handed_off" in outcomes
    assert "no_intent" in outcomes
    for stage in (
        "abandoned_at_product",
        "abandoned_at_lead",
        "abandoned_at_shipping",
        "abandoned_at_confirmation",
        "abandoned_at_payment",
    ):
        assert stage in outcomes


def test_summary_schema_language_only_es_or_en():
    assert SUMMARY_SCHEMA["schema"]["properties"]["language"]["enum"] == ["es", "en"]


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------
def test_system_prompt_lists_catalog():
    product_map = {
        "uuid-cafe-001": "Café Arenillo",
        "uuid-cafe-002": "Café Honey",
    }
    out = _build_system_prompt(product_map)
    assert "uuid-cafe-001: Café Arenillo" in out
    assert "uuid-cafe-002: Café Honey" in out
    assert "products_discussed" in out


def test_system_prompt_handles_empty_catalog():
    out = _build_system_prompt({})
    assert "catálogo vacío" in out


def test_system_prompt_explains_pending_intent_rule():
    out = _build_system_prompt({"u1": "X"})
    assert "pending_intent" in out
    assert "null" in out


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------
def _fake_message(direction: str, content: str):
    return SimpleNamespace(direction=direction, content=content)


def _fake_conversation(
    state="closed",
    extracted=None,
    checkpoint="payment_confirmed",
    pct=80,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
        extracted_context=extracted or {},
        current_checkpoint=checkpoint,
        progress_pct=pct,
    )


def test_user_prompt_includes_state_and_checkpoint():
    conv = _fake_conversation(state="human_handoff", checkpoint="payment_confirmed", pct=100)
    out = _build_user_prompt(conv, [_fake_message("inbound", "hola")], {})
    assert "ESTADO FINAL: human_handoff" in out
    assert "CHECKPOINT FINAL: payment_confirmed" in out
    assert "PROGRESO: 100%" in out


def test_user_prompt_lists_extracted_data():
    conv = _fake_conversation(extracted={"full_name": "Juan", "phone": "3001234567"})
    out = _build_user_prompt(conv, [_fake_message("inbound", "hola")], {})
    assert "full_name: Juan" in out
    assert "phone: 3001234567" in out


def test_user_prompt_handles_empty_extracted():
    conv = _fake_conversation(extracted={})
    out = _build_user_prompt(conv, [_fake_message("inbound", "hola")], {})
    assert "(ninguno)" in out


def test_user_prompt_labels_speakers():
    conv = _fake_conversation()
    msgs = [
        _fake_message("inbound", "Hola, quiero café"),
        _fake_message("outbound", "Claro, te ayudo"),
    ]
    out = _build_user_prompt(conv, msgs, {})
    assert "[CLIENTE] Hola, quiero café" in out
    assert "[AGENTE] Claro, te ayudo" in out


def test_user_prompt_truncates_very_long_messages():
    conv = _fake_conversation()
    long_text = "a" * 800
    out = _build_user_prompt(conv, [_fake_message("inbound", long_text)], {})
    # Should appear truncated with ellipsis, not full 800 chars
    assert "…" in out
    assert "a" * 800 not in out


def test_user_prompt_collapses_newlines():
    conv = _fake_conversation()
    out = _build_user_prompt(
        conv, [_fake_message("inbound", "linea1\nlinea2\nlinea3")], {}
    )
    assert "linea1 linea2 linea3" in out


def test_user_prompt_carries_full_order_after_p2():
    """P4 depends on P2: now that quantity/grind persist to extracted_context,
    a reconstructed summary input sees the complete order, not just product_id."""
    conv = _fake_conversation(
        extracted={
            "product_id": "p-uuid",
            "quantity": 2,
            "grind_preference": "grano",
        }
    )
    out = _build_user_prompt(conv, [_fake_message("inbound", "hola")], {})
    assert "quantity: 2" in out
    assert "grind_preference: grano" in out


# ---------------------------------------------------------------------------
# needs_summary
# ---------------------------------------------------------------------------
def test_needs_summary_true_when_profile_empty():
    assert needs_summary({}, uuid.uuid4()) is True
    assert needs_summary(None, uuid.uuid4()) is True


def test_needs_summary_true_when_no_summary_yet():
    profile = {"full_name": "Juan"}
    assert needs_summary(profile, uuid.uuid4()) is True


def test_needs_summary_true_when_summary_is_for_a_different_conversation():
    other = uuid.uuid4()
    profile = {"last_conversation_summary": {"conversation_id": str(other)}}
    assert needs_summary(profile, uuid.uuid4()) is True


def test_needs_summary_false_when_summary_matches_conversation():
    conv_id = uuid.uuid4()
    profile = {"last_conversation_summary": {"conversation_id": str(conv_id)}}
    assert needs_summary(profile, conv_id) is False


# ---------------------------------------------------------------------------
# P4 — lazy-compaction failure is swallowed but must be OBSERVABLE.
#
# Reproduces summarize_conversation against a 4-message fixture (the shape of
# the 3-may conversation) with a summarizer that raises — the same path that
# silently emptied client_users.profile in prod. Asserts the failure now:
#   - returns None (chat turn still survives — never break the caller), and
#   - is logged at ERROR with a stack trace, and
#   - increments the failure counter (DEUDA #3 observability).
# ---------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, scalar=None, scalars_list=None):
        self._scalar = scalar
        self._scalars_list = scalars_list or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._scalars_list


class _FakeSession:
    """Returns queued results in call order. Enough for the read-only path of
    summarize_conversation up to the (failing) LLM call."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def execute(self, *args, **kwargs):
        result = self._results[self.calls]
        self.calls += 1
        return result


def _four_message_fixture():
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        client_user_id=uuid.uuid4(),
        state="active",
        current_checkpoint="product_matched",
        progress_pct=20,
        extracted_context={"product_id": "p-uuid", "quantity": 2},
    )
    messages = [
        SimpleNamespace(direction="inbound", content="Hola, venden café?"),
        SimpleNamespace(direction="outbound", content="Sí, Café Arenillo 340g."),
        SimpleNamespace(direction="inbound", content="Quiero 2 bolsas en grano"),
        SimpleNamespace(direction="outbound", content="Perfecto, ¿a qué ciudad?"),
    ]
    session = _FakeSession([
        _FakeResult(scalar=conv),            # select(Conversation)
        _FakeResult(scalars_list=messages),  # select(Message)
        _FakeResult(scalars_list=[]),        # select(Product)
    ])
    return session, conv


def test_compaction_failure_is_swallowed_but_returns_none():
    session, conv = _four_message_fixture()

    async def boom(system_prompt, user_prompt):
        # Representative of a prod failure (egress block / 4xx / parse).
        raise ConnectionError("simulated failure reaching api.openai.com")

    before = get_summary_failure_count()
    result = asyncio.run(summarize_conversation(session, conv.id, llm=boom))

    assert result is None  # caller (ingest) keeps working
    assert get_summary_failure_count() == before + 1


def test_compaction_failure_is_logged_at_error_with_traceback(caplog):
    session, conv = _four_message_fixture()

    async def boom(system_prompt, user_prompt):
        raise ConnectionError("simulated failure reaching api.openai.com")

    with caplog.at_level(logging.ERROR, logger="app.services.conversation_summary"):
        asyncio.run(summarize_conversation(session, conv.id, llm=boom))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "compaction failure must log at ERROR, not warning"
    rec = errors[-1]
    assert "LLM call FAILED" in rec.getMessage()
    assert "ConnectionError" in rec.getMessage()
    assert rec.exc_info is not None  # stack trace attached
