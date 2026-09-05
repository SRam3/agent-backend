"""P29 fase 2 — the operator-echo surface (ADR-013).

What is pinned here, in order of how much it would hurt to lose:

1. **The invariant.** An echo may write a message and nothing else. No
   checkpoint, no slot, no state transition, no `strategy_version` bump, no LLM
   call. That is the reason the surface exists separately from `/ingest/message`
   at all, and `test_echo_never_touches_the_conversation_context` is the test
   that protects it. If it ever fails, the design is gone, not just the code.

2. **Authorship and the wamid.** `direction` stays `outbound` — from the
   business, this message went out — and `author='operator'` carries the part
   that was missing. The echo's wamid goes into `chakra_message_id`, which makes
   these the first outbound rows in the system with a real Meta id (deuda #11).

3. **Idempotency.** Meta redelivers. The UNIQUE index rejects it and the
   endpoint reports `duplicate`, not an error.

4. **Which conversation it lands in.** Not the ingest's 24h window: the last
   conversation whatever its state, because the post-sale case is the one that
   justified P29 over P31. And never a compaction, because an operator message
   must not cost an LLM call.

Pure tests: the session is a stub that records what was asked and replays canned
answers, per the repo convention that tests/services needs no DB.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.models.core import AuditLog, Client, ClientUser, Conversation, Message
from app.services.ingest_operator_echo import (
    ClientNotFoundError,
    DuplicateMessageError,
    ingest_operator_echo,
)

CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_ID = uuid.UUID("59cd973e-a94d-4a2f-a228-9ce5d74fe2ac")
CONV_ID = uuid.UUID("90aa2b87-a773-4ebb-98e2-436ee515497c")
BSUID = "CO.0000000000001234"

#: The echo of 2026-08-19 22:14:31Z — the delivery promise the system never knew
#: existed. Everything below is shaped after the payload of `exec 10678`.
ECHO_WAMID = "wamid.HBgMNTczMDAwMDAwMDAwFQIAERgSMEQwMDAwMDAwMDAwMDAwMDAA"
ECHO_TEXT = (
    "mañana nos entregan el café recién tostado… ¿podríamos realizarte la "
    "entrega pasado mañana?"
)
ECHO_AT = datetime(2026, 8, 19, 22, 14, 31, tzinfo=timezone.utc)


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
    """Replays canned results and records what was added/flushed."""

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
                f"the echo service ran past the canned results "
                f"(statement #{len(self.statements)}); it went further than "
                "this test expects — which for this module is the whole point"
            )
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    # -- helpers for assertions --------------------------------------------
    def added_of(self, kind: str) -> list:
        return [o for o in self.added if type(o).__name__ == kind]

    @property
    def sql(self) -> str:
        return " ".join(str(s).lower() for s in self.statements)

    @property
    def writes_sql(self) -> str:
        """Only the statements that CHANGE something. Asserting against the
        full SQL is useless here: `select(Conversation)` lists every column of
        the table, `strategy_version` included, so a naive substring check
        passes on a row we merely read."""
        return " ".join(
            str(s).lower()
            for s in self.statements
            if s.__class__.__name__.lower().startswith(("insert", "update", "delete"))
        )


# ---------------------------------------------------------------------------
# Fixtures as plain builders
# ---------------------------------------------------------------------------
def _client() -> Client:
    return Client(
        id=CLIENT_ID,
        is_active=True,
        business_rules={},
        system_prompt_template="",
        ai_model="gpt-4o-mini",
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


def _conversation(state: str = "active", **kw) -> Conversation:
    conv = Conversation(
        id=CONV_ID,
        client_id=CLIENT_ID,
        client_user_id=USER_ID,
        state=state,
        extracted_context=kw.get("extracted_context", {"full_name": "M. O."}),
        strategy_version=kw.get("strategy_version", 23),
        message_count=53,
        last_message_at=ECHO_AT - timedelta(minutes=3),
    )
    return conv


def _results(conversation=None, *, duplicate=None, user=None):
    """Canned results for every session.execute() of a happy-path echo.

      1 client lookup   2 idempotency   3 resolve client_user
      4 last conversation   5 counter update
    """
    return [
        _client(),
        duplicate,
        user if user is not None else _client_user(),
        conversation,
        None,
    ]


def _run(session, *, content: str = ECHO_TEXT, client_id=CLIENT_ID, **kw):
    return asyncio.run(
        ingest_operator_echo(
            session=session,
            client_id=client_id,
            chakra_message_id=kw.get("chakra_message_id", ECHO_WAMID),
            content=content,
            bsuid=kw.get("bsuid", BSUID),
            phone_number=kw.get("phone_number"),
            display_name=kw.get("display_name", "M. O."),
            message_type=kw.get("message_type", "text"),
            timestamp=kw.get("timestamp", ECHO_AT),
        )
    )


# ===========================================================================
# 1 — the row it writes
# ===========================================================================
def test_echo_persists_one_operator_authored_outbound_row():
    """The payload of exec 10678 becomes exactly one message, authored."""
    session = FakeSession(_results(_conversation()))

    result = _run(session)

    messages = session.added_of("Message")
    assert len(messages) == 1
    msg = messages[0]
    assert msg.author == "operator"
    assert msg.direction == "outbound"
    assert msg.chakra_message_id == ECHO_WAMID
    assert msg.content == ECHO_TEXT
    assert msg.conversation_id == CONV_ID
    assert msg.client_id == CLIENT_ID
    assert result["status"] == "ingested"


def test_echo_is_stamped_with_metas_clock_not_ours():
    """ADR-011 §5.2.4: inbound rows carry Meta's clock. An echo stamped with
    now() would interleave wrongly and the LLM would read the conversation out
    of order — which is the exact failure P29 exists to fix."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert session.added_of("Message")[0].created_at == ECHO_AT


def test_echo_without_a_timestamp_falls_back_to_wall_clock():
    """Degradation, not a crash: a payload with no timestamp still persists."""
    session = FakeSession(_results(_conversation()))
    before = datetime.now(timezone.utc)

    _run(session, timestamp=None)

    assert session.added_of("Message")[0].created_at >= before


def test_echo_writes_its_own_audit_event():
    """Deuda #19 in reverse: this path leaves a trail from day one."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    events = session.added_of("AuditLog")
    assert len(events) == 1
    assert events[0].event_type == "operator_echo_ingested"
    assert events[0].actor_type == "operator"
    assert events[0].new_value["chakra_message_id"] == ECHO_WAMID
    assert events[0].new_value["conversation_id"] == str(CONV_ID)


def test_audit_event_records_the_length_never_the_body():
    """PII: the operator's text is staff communication. Length, never content."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    payload = session.added_of("AuditLog")[0].new_value
    assert payload["content_length"] == len(ECHO_TEXT)
    assert ECHO_TEXT not in str(payload)


def test_the_echo_body_is_never_logged(caplog):
    """Same rule at the logging boundary. The wamid and the length, not the text."""
    session = FakeSession(_results(_conversation()))

    with caplog.at_level(logging.DEBUG):
        _run(session)

    assert ECHO_TEXT not in caplog.text
    assert ECHO_WAMID in caplog.text


# ===========================================================================
# 2 — idempotency
# ===========================================================================
def test_a_redelivered_echo_raises_duplicate_and_writes_nothing():
    """Meta redelivers. The UNIQUE index on chakra_message_id makes this free."""
    session = FakeSession(_results(_conversation(), duplicate=uuid.uuid4()))

    with pytest.raises(DuplicateMessageError):
        _run(session)

    assert session.added_of("Message") == []
    assert session.added_of("AuditLog") == []


def test_the_duplicate_check_is_the_wamid_not_the_text():
    """Two different messages with identical text are two messages."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert "chakra_message_id" in session.sql


# ===========================================================================
# 3 — identity
# ===========================================================================
def test_an_unknown_customer_is_created_from_the_echo():
    """The operator may have written FIRST, to someone we have never seen."""
    created = _client_user()
    # _resolve_client_user with a bsuid and no phone: one lookup, then insert.
    session = FakeSession([_client(), None, None, created, _conversation(), None])

    _run(session)

    assert session.added_of("Message")[0].conversation_id == CONV_ID


def test_identity_resolution_goes_through_the_bsuid():
    """The echo carries `to_user_id` — the recipient — in the same BSUID
    namespace as an inbound `from_user_id`. Present in 247 of 247 payloads."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert "bsuid" in session.sql


# ===========================================================================
# 4 — which conversation it lands in
# ===========================================================================
def test_an_echo_attaches_to_a_closed_conversation_instead_of_forking_one():
    """The case that justified P29 over P31: the operator coordinates delivery
    an hour after the sale closed. Filtering on state would split the very
    dialogue we are trying to make whole."""
    session = FakeSession(_results(_conversation(state="closed")))

    _run(session)

    assert session.added_of("Conversation") == []
    assert session.added_of("Message")[0].conversation_id == CONV_ID


def test_the_conversation_lookup_does_not_filter_on_the_24h_window():
    """The ingest's window is about starting a new customer session. An echo
    starts nothing."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert "last_message_at >" not in session.sql


def test_a_customer_with_no_conversation_at_all_gets_a_bare_one():
    """Not a crash: messages.conversation_id is NOT NULL, so the echo needs
    somewhere to live. Bare means no seed and, above all, no compaction."""
    session = FakeSession(_results(conversation=None))

    _run(session)

    created = session.added_of("Conversation")
    assert len(created) == 1
    assert created[0].extracted_context == {}
    assert created[0].strategy_version == 0
    assert created[0].state == "active"


def test_an_echo_never_triggers_the_lazy_compaction():
    """An operator message must never cost an LLM call.

    The ingest compacts when it opens a conversation. This path must not, and
    the proof is structural rather than behavioural: the module does not import
    the summariser at all, so there is no call site to reach.
    """
    session = FakeSession(_results(conversation=None))

    _run(session)

    import app.services.ingest_operator_echo as echo_mod

    source = inspect.getsource(echo_mod)
    assert "summarize_conversation" not in source
    assert "needs_summary" not in source
    assert "SummarizerLLM" not in source


def test_the_echo_bumps_the_counters_so_the_session_window_stays_open():
    """While a human is in the chat, the customer's reply must land in THIS
    conversation, not a fresh one."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    sql = session.writes_sql
    assert "update conversations" in sql
    assert "message_count" in sql
    assert "last_message_at" in sql
    # GREATEST guards against a late redelivery dragging the clock backwards.
    assert "greatest" in sql


# ===========================================================================
# 5 — THE INVARIANT: an echo is context, never fact
# ===========================================================================
def test_echo_never_touches_the_conversation_context():
    """The test that protects the central invariant of ADR-013.

    The operator writes "listo, ya quedó confirmado". That is prose from a human,
    not a structured claim, and nothing in it may become a checkpoint. If this
    test ever fails, the design is gone — not just the code.
    """
    conversation = _conversation(extracted_context={"full_name": "M. O."})
    before = dict(conversation.extracted_context)
    session = FakeSession(_results(conversation))

    _run(session, content="listo, ya quedó confirmado")

    assert conversation.extracted_context == before
    assert "user_confirmation" not in conversation.extracted_context
    assert "payment_confirmation" not in conversation.extracted_context


def test_echo_never_bumps_the_strategy_version():
    """Until P34, an echo does not invalidate the in-flight turn. Bumping the
    version here would produce the system's first ever 409, which today lands in
    n8n as an indistinguishable `backend_error`."""
    conversation = _conversation(strategy_version=23)
    session = FakeSession(_results(conversation))

    _run(session)

    assert conversation.strategy_version == 23
    assert "strategy_version" not in session.writes_sql


def test_echo_never_transitions_the_state():
    """The pause is suppression with an expiry, not a move to human_handoff —
    that state has no way back (ADR-007)."""
    conversation = _conversation(state="active")
    session = FakeSession(_results(conversation))

    _run(session)

    assert conversation.state == "active"


def test_echo_writes_exactly_two_rows_and_no_more():
    """Whole-module budget: one Message, one AuditLog. Anything else that starts
    getting added here is a checkpoint sneaking in by another name."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert [type(o).__name__ for o in session.added] == ["Message", "AuditLog"]


# ===========================================================================
# 6 — tenancy
# ===========================================================================
def test_an_echo_for_an_unknown_tenant_is_rejected_before_anything_is_written():
    """Multi-tenant isolation is enforced per application, not by RLS (deuda #5),
    so the client lookup is the boundary and it comes first."""
    session = FakeSession([None])

    with pytest.raises(ClientNotFoundError):
        _run(session, client_id=OTHER_CLIENT_ID)

    assert session.added == []


def test_every_write_carries_the_client_id_of_the_caller():
    """The X-Client-ID of the request, never a value read out of the payload."""
    session = FakeSession(_results(_conversation()))

    _run(session)

    assert session.added_of("Message")[0].client_id == CLIENT_ID
    assert session.added_of("AuditLog")[0].client_id == CLIENT_ID
