"""Suppressed ingest turns — the response contract, and the content guard.

Two things are pinned here, in the order they were built:

1. **The contract** (deuda #13). Every path that decides "do not answer this"
   must still return a COMPLETE IngestMessageResponse. The debounce path used
   to return `{"should_respond": False, "reason": "debounce"}`, which the
   endpoint fed to `IngestMessageResponse(**result)` — 8 missing fields, 500
   on every single coalescence. The path had never once returned a valid
   response; n8n only survived because `POST Ingest Message` carries
   `continueOnFail: true`, so the AxiosError became an item with no
   `should_respond` and fell through to Stop.

2. **The guard** (mitad barata de P16). A message with no readable content
   persists but generates no turn. Measured against prod on 2026-08-22: 20 of
   79 inbound had nothing to read (16 `unsupported`, 3 `audio`, 1 `edit`) and
   the bot answered all of them blind.

Pure tests: the session is a stub that records what was asked and replays
canned answers, per the repo convention that tests/services needs no DB.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.api.v1.ingest import IngestMessageResponse
from app.models.core import Client, ClientUser, Conversation
from app.services import ingest as ingest_mod
from app.services.ingest import (
    build_suppressed_response,
    ingest_message,
    is_unreadable,
)

CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("59cd973e-a94d-4a2f-a228-9ce5d74fe2ac")
CONV_ID = uuid.UUID("90aa2b87-a773-4ebb-98e2-436ee515497c")
NOW = datetime(2026, 8, 19, 22, 15, 0, tzinfo=timezone.utc)
BSUID = "CO.0000000000001234"


# ---------------------------------------------------------------------------
# Session stub
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row


class FakeSession:
    """Replays canned results and records what was added/flushed/committed."""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []
        self.added = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if not self._results:
            raise AssertionError(
                f"ingest ran past the canned results (statement #{len(self.statements)}); "
                "the code under test went further than this test expects"
            )
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    # -- helpers for assertions --------------------------------------------
    @property
    def added_messages(self):
        return [o for o in self.added if type(o).__name__ == "Message"]


def _client() -> Client:
    return Client(
        id=CLIENT_ID,
        is_active=True,
        business_rules={},
        system_prompt_template="",
        ai_model="gpt-4o-mini",
    )


def _client_user() -> ClientUser:
    return ClientUser(
        id=USER_ID,
        client_id=CLIENT_ID,
        bsuid=BSUID,
        display_name="M. O.",
        is_blocked=False,
        profile={},
    )


def _conversation() -> Conversation:
    return Conversation(
        id=CONV_ID,
        client_id=CLIENT_ID,
        client_user_id=USER_ID,
        state="active",
        extracted_context={},
        strategy_version=23,
        message_count=53,
        last_message_at=NOW - timedelta(minutes=1),
    )


#: Zero-based index of the debounce lookahead among the statements ingest emits.
#: Named rather than inlined because ADR-013 inserted the operator-pause lookup
#: right before it and shifted every hard-coded index in this file by one.
_DEBOUNCE_LOOKAHEAD = 7


def _results_up_to_debounce_check(newer_message_id, *, last_echo_at=None):
    """Canned results for every session.execute() from entry to the debounce
    lookahead, inclusive.

      1 client lookup   2 idempotency   3 resolve client_user
      4 conversation    5 advisory lock  6 counter update
      7 operator-pause lookup  <- last_echo_at (ADR-013; None = no operator)
      8 debounce lookahead     <- newer_message_id

    The list is positional, so step 7 landing in the middle is exactly why
    every test that reaches the debounce goes through this one helper.
    """
    return [
        _client(),
        None,
        _client_user(),
        _conversation(),
        None,
        None,
        last_echo_at,
        newer_message_id,
    ]


def _run(session, *, content: str, message_type: str = "text", monkeypatch=None):
    """Drive ingest_message with the 5-second debounce sleep neutralised."""

    async def _no_sleep(_seconds):
        return None

    original_sleep = asyncio.sleep
    ingest_mod.asyncio.sleep = _no_sleep
    try:
        return asyncio.run(
            ingest_message(
                session=session,
                client_id=CLIENT_ID,
                chakra_message_id="wamid.TEST",
                content=content,
                bsuid=BSUID,
                display_name="M. O.",
                message_type=message_type,
                timestamp=NOW,
            )
        )
    finally:
        ingest_mod.asyncio.sleep = original_sleep


# ===========================================================================
# COMMIT 1 — the response contract (deuda #13)
# ===========================================================================
def test_debounce_returns_a_valid_response_not_a_two_key_dict():
    """The bug, falsified: this used to return 2 keys and 500 the endpoint."""
    session = FakeSession(_results_up_to_debounce_check(uuid.uuid4()))

    result = _run(session, content="Mejor con el domicilio")

    assert result["should_respond"] is False
    assert result["reason"] == "debounce"
    # The endpoint does exactly this, and it used to raise.
    response = IngestMessageResponse(**result)
    assert response.should_respond is False
    assert response.reason == "debounce"


def test_debounce_response_carries_the_real_conversation_not_a_placeholder():
    """n8n's "IF Should Respond" reads conversation_state as well as
    should_respond, so the state has to be real, not defaulted."""
    session = FakeSession(_results_up_to_debounce_check(uuid.uuid4()))

    result = _run(session, content="Listo")

    assert result["conversation_id"] == CONV_ID
    assert result["conversation_state"] == "active"
    assert result["strategy_version"] == 23


def test_suppressed_response_has_every_required_field():
    """Field-by-field, against the model's own definition — so that adding a
    required field to IngestMessageResponse fails here instead of in prod."""
    payload = build_suppressed_response("debounce", _conversation())

    for name, field in IngestMessageResponse.model_fields.items():
        if field.is_required():
            assert name in payload, f"suppressed response is missing {name!r}"

    IngestMessageResponse(**payload)  # must not raise


def test_duplicate_path_still_suppresses_with_a_dummy_conversation():
    """Regression on the path that already worked: same behaviour, now built
    by the shared helper and labelled with its reason."""
    payload = build_suppressed_response("duplicate")
    response = IngestMessageResponse(**payload)

    assert response.should_respond is False
    assert response.reason == "duplicate"
    assert response.conversation_state == "active"
    assert response.strategy_version == 0
    assert response.strategy_directive == ""
    assert response.strategy_meta == {}
    assert response.client_config == {}
    assert response.user_context == {}
    assert response.recent_messages == []
    assert isinstance(response.conversation_id, uuid.UUID)


def test_reason_is_empty_on_a_normal_answered_turn():
    """The field must not leak a suppression reason onto real turns."""
    assert IngestMessageResponse.model_fields["reason"].is_required() is False
    assert (
        IngestMessageResponse(
            should_respond=True,
            conversation_id=CONV_ID,
            conversation_state="active",
            strategy_directive="d",
            strategy_meta={},
            strategy_version=1,
            client_config={},
            user_context={},
            recent_messages=[],
        ).reason
        == ""
    )


# ===========================================================================
# COMMIT 2 — the unreadable-content guard (mitad barata de P16)
# ===========================================================================
def _results_up_to_guard():
    """Canned results for every session.execute() before the guard fires.

    Same as the debounce list minus the lookahead: the guard returns before
    the 5-second wait, so that query never happens. FakeSession raises if the
    code asks for more, which is how we prove the wait was skipped.
    """
    return [_client(), None, _client_user(), _conversation(), None, None]


@pytest.mark.parametrize(
    "message_type,content,label",
    [
        ("edit", "", "el evento edit del 2026-08-19"),
        ("audio", "", "el audio de las 22:15 UTC"),
        ("unsupported", "", "unsupported"),
        ("image", "", "imagen sin caption"),
        ("text", "   ", "solo espacios"),
        ("text", "\n\t ", "solo whitespace"),
    ],
)
def test_unreadable_message_takes_no_turn(message_type, content, label):
    session = FakeSession(_results_up_to_guard())

    result = _run(session, content=content, message_type=message_type)

    assert result["should_respond"] is False, label
    assert result["reason"] == "unreadable_content", label
    IngestMessageResponse(**result)  # must be a valid response, not a stub


def test_unreadable_message_is_still_persisted():
    """Persist, THEN suppress. The next turn has to find it in the history."""
    session = FakeSession(_results_up_to_guard())

    _run(session, content="", message_type="audio")

    assert len(session.added_messages) == 1
    message = session.added_messages[0]
    assert message.content == ""
    assert message.message_type == "audio"  # the real type, not normalised away
    assert message.direction == "inbound"
    assert message.chakra_message_id == "wamid.TEST"
    assert session.commits >= 1, "the message must be committed before suppressing"


def test_unreadable_message_never_computes_a_strategy(monkeypatch):
    """No directive means no turn, which means n8n never calls the LLM."""
    calls = []
    monkeypatch.setattr(
        ingest_mod._engine,
        "compute",
        lambda *a, **k: calls.append(a) or pytest.fail("strategy was computed"),
    )
    session = FakeSession(_results_up_to_guard())

    _run(session, content="", message_type="unsupported")

    assert calls == []


def test_unreadable_message_skips_the_five_second_wait():
    """The guard sits before the debounce, so an unreadable message does not
    hold a connection open for 5s. FakeSession runs out of canned results if
    the lookahead query is attempted."""
    session = FakeSession(_results_up_to_guard())

    _run(session, content="", message_type="image")

    # 6 executes: client, idempotency, client_user, conversation, lock, counters.
    assert len(session.statements) == 6


def test_readable_text_is_untouched_by_the_guard():
    """The regression that matters: a normal message still takes its turn.

    Driven only as far as the debounce lookahead — past it the function needs
    a real catalog and product rows, which is another test's job. Reaching the
    lookahead at all proves the guard let the message through.
    """
    session = FakeSession(_results_up_to_debounce_check(uuid.uuid4()))

    result = _run(session, content="Quiero 2 libras de café en grano")

    assert result["reason"] == "debounce"  # not "unreadable_content"
    assert len(session.statements) == _DEBOUNCE_LOOKAHEAD + 1  # the lookahead DID run


def test_debounce_ignores_newer_messages_that_cannot_take_a_turn():
    """Text, then a voice note. Without the filter the text defers to the
    audio and the audio suppresses itself, so a real question goes unanswered.

    The lookahead returns nothing (the audio is filtered out), so the turn is
    NOT suppressed and ingest carries on into the strategy computation — which
    this stub is not provisioned for. Running out of canned results *is* the
    assertion: an early return would have ended the call at statement 7.
    """
    session = FakeSession(_results_up_to_debounce_check(None))  # no readable newer

    with pytest.raises(AssertionError, match="ran past the canned results"):
        _run(session, content="¿Me lo pueden enviar hoy?")

    assert len(session.statements) == _DEBOUNCE_LOOKAHEAD + 2, (
        "should have proceeded past the lookahead"
    )


def test_debounce_lookahead_filters_on_content_in_sql():
    """Pin the filter in the emitted SQL, so removing it fails here."""
    session = FakeSession(_results_up_to_debounce_check(uuid.uuid4()))

    _run(session, content="hola")

    lookahead = str(session.statements[_DEBOUNCE_LOOKAHEAD]).lower()
    assert "btrim" in lookahead, "debounce lookahead lost its readable-content filter"


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("content", ["", "   ", "\n", "\t", " \n\t ", None])
def test_is_unreadable_true(content):
    assert is_unreadable(content) is True


@pytest.mark.parametrize("content", ["hola", " hola ", "0", "?", "👍"])
def test_is_unreadable_false(content):
    assert is_unreadable(content) is False
