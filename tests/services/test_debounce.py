"""Tests for the rearmable-debounce decision function.

Pure Python — exercises evaluate_debounce_state across the four key
scenarios. The async polling loop (_wait_for_silence) is exercised in
integration tests (pending) since it requires a DB and asyncio.sleep.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.ingest import (
    DEBOUNCE_SILENCE_REQUIRED,
    evaluate_debounce_state,
)


UTC = timezone.utc


def _ts(seconds_from_t0: float) -> datetime:
    """Helper: deterministic timestamp at T0 + seconds."""
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds_from_t0)


# ---------------------------------------------------------------------------
# Bail scenarios — a newer inbound arrived
# ---------------------------------------------------------------------------
def test_bail_when_newer_inbound_exists():
    """Customer sent another message after mine — let it respond instead."""
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(3),  # newer than me
        now=_ts(5),
    )
    assert decision == "bail"


def test_bail_when_newer_inbound_by_microseconds():
    """Edge case: newer by even a microsecond should bail."""
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0.000001),
        now=_ts(5),
    )
    assert decision == "bail"


def test_bail_takes_priority_over_silence():
    """Even if SILENCE_REQUIRED has passed, bail if a newer message exists."""
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(3),
        now=_ts(100),  # tons of silence since the newer one
    )
    assert decision == "bail"


# ---------------------------------------------------------------------------
# Proceed scenarios — I am the latest and silence is enough
# ---------------------------------------------------------------------------
def test_proceed_when_silence_achieved_with_self_as_latest():
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0),  # I am the latest
        now=_ts(DEBOUNCE_SILENCE_REQUIRED),
    )
    assert decision == "proceed"


def test_proceed_when_silence_well_beyond_required():
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0),
        now=_ts(60),
    )
    assert decision == "proceed"


def test_proceed_when_no_inbound_visible_yet():
    """Edge case: query returned NULL (rare, e.g. row not yet visible).
    Falls back to using my own timestamp as the silence anchor."""
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=None,
        now=_ts(DEBOUNCE_SILENCE_REQUIRED + 1),
    )
    assert decision == "proceed"


# ---------------------------------------------------------------------------
# Wait scenarios — I am the latest but not enough silence yet
# ---------------------------------------------------------------------------
def test_wait_when_silence_not_yet_achieved():
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0),
        now=_ts(DEBOUNCE_SILENCE_REQUIRED - 0.001),
    )
    assert decision == "wait"


def test_wait_when_just_arrived():
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0),
        now=_ts(0.5),
    )
    assert decision == "wait"


def test_wait_when_no_inbound_and_no_silence_yet():
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=None,
        now=_ts(1),
    )
    assert decision == "wait"


# ---------------------------------------------------------------------------
# Silence_required override
# ---------------------------------------------------------------------------
def test_silence_required_can_be_overridden():
    """Useful for tests and for tuning under load."""
    decision = evaluate_debounce_state(
        my_msg_timestamp=_ts(0),
        latest_inbound_ts=_ts(0),
        now=_ts(2),
        silence_required_seconds=1.0,
    )
    assert decision == "proceed"


# ---------------------------------------------------------------------------
# Scenario from the May 2 bug — replayed
# ---------------------------------------------------------------------------
def test_may2_bug_replay_first_message_should_bail():
    """In the original bug:
       - M1 'Tienes una foto?' at 10:53:05
       - M2 'Quiero ver la presentación' at 10:53:10
    Old logic: M1 waited 5s → 10:53:10, found nothing strictly newer → proceeded.
    M2 then ran independently → also proceeded → two photos sent.
    New logic with rearmable poll: M1's poll at 10:53:07 (poll_interval=2s)
    sees M2 already in the table → bail."""
    m1_ts = _ts(0)        # 10:53:05
    m2_ts = _ts(5)        # 10:53:10 — newer than m1
    poll_at = _ts(7)      # m1's first poll, 2s after entering debounce
    decision = evaluate_debounce_state(
        my_msg_timestamp=m1_ts,
        latest_inbound_ts=m2_ts,
        now=poll_at,
    )
    assert decision == "bail"


def test_may2_bug_replay_second_message_proceeds_after_silence():
    """Same scenario: M2 keeps polling. After 5s of silence since M2
    arrived (i.e. T=10s on M2 timeline), M2 proceeds."""
    m2_ts = _ts(5)
    decision = evaluate_debounce_state(
        my_msg_timestamp=m2_ts,
        latest_inbound_ts=m2_ts,    # m2 is the latest
        now=_ts(10),                # 5s after m2
    )
    assert decision == "proceed"
