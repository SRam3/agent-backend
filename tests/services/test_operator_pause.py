"""P29 fase 3 — the operator-presence pause (ADR-013 §4).

The bot stays quiet while a human is answering the chat, and comes back on its
own. Four properties are pinned here and each one is a thing that went wrong in
production on 2026-08-19:

1. **It suppresses.** An echo within the window means the next turn returns
   `should_respond: false, reason: "operator_active"`.
2. **It expires, and it expires from the LAST echo.** A pause that does not
   expire is not a pause; a pause measured from the first echo would end while
   the operator is still typing.
3. **It leaves a trail.** Three suppression paths already return before the
   audit log, which is why 36 inbound have no `message_ingest` event and a turn
   silenced by the guard cannot be told from one lost to a 500 (deuda #19).
   This path does not get to add a fourth silent hole.
4. **The more specific reason wins.** An unreadable medium arriving during the
   pause is still suppressed as `unreadable_content`.

Plus the half that makes the pause worth having: when the bot does come back,
it can see what the operator said.

Pure tests: the session is a stub that replays canned answers, per the repo
convention that tests/services needs no DB.
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
from app.models.core import Client, ClientUser, Conversation, Message
from app.services import ingest as ingest_mod
from app.services.ingest import (
    DEFAULT_OPERATOR_PAUSE_MINUTES,
    _content_for_prompt,
    _operator_pause_minutes,
    author_of,
    ingest_message,
)

CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("59cd973e-a94d-4a2f-a228-9ce5d74fe2ac")
CONV_ID = uuid.UUID("90aa2b87-a773-4ebb-98e2-436ee515497c")
BSUID = "CO.0000000000001234"

#: 2026-08-19 22:15 — one minute after the operator promised the delivery.
NOW = datetime(2026, 8, 19, 22, 15, 0, tzinfo=timezone.utc)

#: Zero-based index of the operator-pause lookup among the statements ingest
#: emits: client, idempotency, resolve user, conversation, lock, counters, THIS.
_PAUSE_LOOKUP = 6


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

    def scalars(self):
        return self

    def all(self):
        return self._row if isinstance(self._row, list) else []


class FakeSession:
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

    def added_of(self, kind: str) -> list:
        return [o for o in self.added if type(o).__name__ == kind]

    @property
    def pause_stmt(self) -> str:
        return str(self.statements[_PAUSE_LOOKUP]).lower()

    @property
    def pause_params(self) -> dict:
        return self.statements[_PAUSE_LOOKUP].compile().params


def _client(business_rules=None) -> Client:
    return Client(
        id=CLIENT_ID,
        is_active=True,
        business_rules=business_rules if business_rules is not None else {},
        system_prompt_template="",
        ai_model="gpt-4o-mini",
        ai_temperature=0.3,
    )


def _client_user() -> ClientUser:
    user = ClientUser()
    user.id = USER_ID
    user.client_id = CLIENT_ID
    user.bsuid = BSUID
    user.display_name = "M. O."
    user.is_blocked = False
    user.profile = {}
    return user


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
        active_goal="close_sale",
    )


def _results_up_to_pause(last_echo_at, *, business_rules=None):
    """Canned results from entry through the operator-pause lookup, inclusive."""
    return [
        _client(business_rules),
        None,
        _client_user(),
        _conversation(),
        None,
        None,
        last_echo_at,
    ]


def _run(session, *, content: str = "¿ya me la puedes compartir?", message_type="text"):
    """Drive ingest_message with the 5-second debounce sleep neutralised."""

    async def _no_sleep(_seconds):
        return None

    original_sleep = ingest_mod.asyncio.sleep
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
# 1 — it suppresses
# ===========================================================================
def test_an_echo_five_minutes_ago_silences_the_next_turn():
    """The operator is answering right now. Stay out of the way."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    result = _run(session)

    assert result["should_respond"] is False
    assert result["reason"] == "operator_active"


def test_the_suppressed_response_is_complete_and_carries_the_real_conversation():
    """n8n's `IF Should Respond` reads conversation_state as well, so a missing
    state is not inert. This is the fourth caller of build_suppressed_response
    and it has to obey the same contract the other three learned in deuda #13."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    result = _run(session)

    response = IngestMessageResponse(**result)  # used to raise on 8 missing fields
    assert response.conversation_id == CONV_ID
    assert response.conversation_state == "active"
    assert response.strategy_version == 23


def test_the_customer_message_is_persisted_before_the_turn_is_suppressed():
    """Suppressing is only safe because the message is already committed: the
    next turn still finds it in recent_messages. Persist, THEN suppress."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert len(session.added_of("Message")) == 1
    assert session.commits == 1


def test_the_pause_skips_the_five_second_debounce_wait():
    """No sense sleeping 5s only to say nothing. The pause is checked BEFORE
    the debounce, and running out of statements right after the lookup is what
    proves the debounce never ran."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert len(session.statements) == _PAUSE_LOOKUP + 1


def test_the_pause_never_computes_a_strategy(monkeypatch):
    """A silenced turn must not bump strategy_version — an echo is context,
    never fact, and until P34 it does not invalidate anything."""
    called = []
    monkeypatch.setattr(
        ingest_mod._engine,
        "compute",
        lambda *a, **kw: called.append(a) or pytest.fail("strategy computed"),
    )
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert called == []


# ===========================================================================
# 2 — it expires, and from the LAST echo
# ===========================================================================
def test_no_echo_in_the_window_lets_the_bot_answer_normally():
    """The no-regression test. Nothing about the pause may touch a chat the
    operator is not in — which is every chat, almost always."""
    session = FakeSession(_results_up_to_pause(None))

    # The turn carries on into the strategy computation, which this stub is not
    # provisioned for. Running out of canned results IS the assertion: a
    # suppression would have ended the call at the lookup.
    with pytest.raises(AssertionError, match="ran past the canned results"):
        _run(session)

    assert len(session.statements) > _PAUSE_LOOKUP + 1


def test_the_window_opens_thirty_minutes_before_the_inbound():
    """The boundary itself, read off the emitted SQL.

    This is the test the mutation exercise targets: flip the comparison and it
    fails here, before any behavioural test gets a chance to pass by accident.
    """
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert "messages.author =" in session.pause_stmt
    assert "messages.created_at >=" in session.pause_stmt

    cutoffs = [
        v for v in session.pause_params.values() if isinstance(v, datetime)
    ]
    assert cutoffs == [NOW - timedelta(minutes=DEFAULT_OPERATOR_PAUSE_MINUTES)]


def test_the_window_is_measured_on_metas_clock_not_ours():
    """Both sides are Meta's clock (ADR-011 §5.2.4): the echo carries Meta's
    timestamp and so does the inbound. Comparing against now() would cross the
    exact seam ADR-011 exists to stop us crossing — and would drift silently."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    cutoff = [v for v in session.pause_params.values() if isinstance(v, datetime)][0]
    # Anchored to the message's timestamp, not to wall time.
    assert cutoff == NOW - timedelta(minutes=DEFAULT_OPERATOR_PAUSE_MINUTES)
    assert cutoff < datetime.now(timezone.utc) - timedelta(days=1)


def test_the_lookup_takes_the_most_recent_echo_so_the_pause_extends():
    """A second echo at minute 20 must restart the window, not let it die 30
    minutes after the first. Ordering DESC and taking one row is what makes the
    signal self-refreshing — the property that made this beat P31's window."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert "order by messages.created_at desc" in session.pause_stmt
    assert "limit" in session.pause_stmt


def test_the_lookup_is_scoped_to_this_conversation():
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    _run(session)

    assert "messages.conversation_id =" in session.pause_stmt


# ===========================================================================
# 3 — the trail
# ===========================================================================
def test_a_silenced_turn_writes_its_own_audit_event():
    """Criterio de aceptación, not a nice-to-have: a turn silenced by the
    operator must be distinguishable in audit_log from one silenced by the
    guard, by debounce, or lost to a 500."""
    last_echo = NOW - timedelta(minutes=5)
    session = FakeSession(_results_up_to_pause(last_echo))

    _run(session)

    events = session.added_of("AuditLog")
    assert len(events) == 1
    assert events[0].event_type == "turn_suppressed"
    assert events[0].new_value["reason"] == "operator_active"
    assert events[0].new_value["last_echo_at"] == last_echo.isoformat()
    assert events[0].new_value["pause_minutes"] == DEFAULT_OPERATOR_PAUSE_MINUTES


# ===========================================================================
# 4 — the more specific reason wins
# ===========================================================================
def test_an_unreadable_medium_during_the_pause_keeps_its_own_reason():
    """A voice note arriving while the operator is typing is suppressed as
    `unreadable_content`, not `operator_active`. The guard runs first because
    its reason says more about what happened."""
    session = FakeSession(_results_up_to_pause(NOW - timedelta(minutes=5)))

    result = _run(session, content="", message_type="audio")

    assert result["reason"] == "unreadable_content"
    # The pause lookup never even ran: the guard returned before it.
    assert len(session.statements) == _PAUSE_LOOKUP


# ===========================================================================
# 5 — the window is configurable per tenant, and fails safe
# ===========================================================================
def test_the_window_is_configurable_per_tenant():
    session = FakeSession(
        _results_up_to_pause(
            NOW - timedelta(minutes=5), business_rules={"operator_pause_minutes": 90}
        )
    )

    _run(session)

    cutoff = [v for v in session.pause_params.values() if isinstance(v, datetime)][0]
    assert cutoff == NOW - timedelta(minutes=90)


@pytest.mark.parametrize(
    "value", [None, "", "treinta", 0, -5, {"minutes": 30}], ids=repr
)
def test_a_malformed_window_falls_back_instead_of_disabling_the_pause(value):
    """Fail safe, not open. A typo in business_rules must not hand the
    conversation back to the bot while a human is answering it."""
    assert (
        _operator_pause_minutes({"operator_pause_minutes": value})
        == DEFAULT_OPERATOR_PAUSE_MINUTES
    )


def test_a_tenant_without_the_key_gets_the_code_default():
    assert _operator_pause_minutes({}) == DEFAULT_OPERATOR_PAUSE_MINUTES
    assert _operator_pause_minutes(None) == DEFAULT_OPERATOR_PAUSE_MINUTES


# ===========================================================================
# 6 — when the bot comes back, it can see what the operator said
# ===========================================================================
def _msg(direction, content, author=None, minutes_ago=0) -> Message:
    return Message(
        id=uuid.uuid4(),
        conversation_id=CONV_ID,
        client_id=CLIENT_ID,
        direction=direction,
        author=author,
        message_type="text",
        content=content,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )


def test_the_operator_marker_reaches_the_text_the_model_reads():
    """Without this, half the value of P29 is lost.

    The Customer/Agent label is rendered by n8n's `Build LLM Prompt` node from
    `direction` alone, so an operator echo would be read back to the LLM as the
    bot's own words — the bot would keep reasoning over a dialogue missing half
    of itself, which is the failure this whole front exists to end.
    """
    operator_msg = _msg(
        "outbound", "te comparto la llave en un momento", author="operator"
    )

    assert _content_for_prompt(operator_msg).startswith("[operador] ")
    assert "te comparto la llave en un momento" in _content_for_prompt(operator_msg)


def test_only_the_operator_gets_a_marker():
    """n8n already labels inbound as Customer and outbound as Agent, and both
    are right. Prefixing them too would double up the label."""
    assert _content_for_prompt(_msg("inbound", "hola", author="customer")) == "hola"
    assert _content_for_prompt(_msg("outbound", "hola", author="bot")) == "hola"


def test_an_empty_operator_message_is_not_turned_into_a_bare_marker():
    """A stray marker with no text behind it is noise in the prompt."""
    assert _content_for_prompt(_msg("outbound", "", author="operator")) == ""
    assert _content_for_prompt(_msg("outbound", None, author="operator")) is None


def test_authorship_of_rows_written_before_migration_015():
    """`author` is nullable because the migration is additive. Pre-015 rows are
    inferred exactly the way the backfill inferred them, so a partially
    backfilled table reads the same as a fully backfilled one."""
    assert author_of(_msg("inbound", "hola", author=None)) == "customer"
    assert author_of(_msg("outbound", "hola", author=None)) == "bot"
    assert author_of(_msg("outbound", "hola", author="operator")) == "operator"


def test_recent_messages_carries_authorship_in_temporal_order():
    """End to end through the ingest: the payload n8n receives shows the whole
    conversation, operator included, in the right order.

    Order works because echoes are stamped with META's clock, the same clock
    the inbound rows carry (ADR-011 §5.2.4). Persisting an echo with now()
    would interleave it wrongly and the model would read the dialogue jumbled.
    """
    history = [
        _msg("inbound", "¿me compartes la llave?", author="customer", minutes_ago=30),
        _msg("outbound", "claro, te la comparto", author="bot", minutes_ago=25),
        _msg(
            "outbound",
            "la llave es 5678, te la mando por aquí",
            author="operator",
            minutes_ago=20,
        ),
        _msg("inbound", "listo, gracias", author="customer", minutes_ago=1),
    ]
    session = FakeSession(
        _results_up_to_pause(None)          # 7 no operator in the window
        + [
            None,                            # 8 debounce lookahead: nothing newer
            None,                            # 9 re-acquire advisory lock
            _conversation(),                 # 10 reload conversation
            None,                            # 11 persist strategy state
            [],                              # 12 product catalog
            list(reversed(history)),         # 13 recent messages (DESC from SQL)
        ]
    )

    result = _run(session, content="listo, gracias")

    assert result["should_respond"] is True
    recent = result["recent_messages"]
    assert [m["author"] for m in recent] == ["customer", "bot", "operator", "customer"]
    # The marker is inside the text, because that is what actually reaches the
    # model: n8n renders its Customer/Agent label from `direction` alone.
    assert recent[2]["content"].startswith("[operador] ")
    assert "5678" in recent[2]["content"]
    # And the operator's row is still outbound — from the business, it went out.
    assert recent[2]["direction"] == "outbound"
    # Oldest first, so the LLM reads the conversation forwards.
    assert [m["created_at"] for m in recent] == sorted(
        m["created_at"] for m in recent
    )
