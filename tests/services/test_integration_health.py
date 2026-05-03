"""Tests for the integration_health service.

Pure Python — covers the pure decision function (_summarize_n8n_freshness).
The DB-touching record_n8n_ping and _count_stuck_conversations are exercised
in integration tests (pending — see CLAUDE.md deuda #1).
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../sales_agent_api"))

from app.services.integration_health import (
    N8N_HEALTHY_WITHIN_SECONDS,
    N8N_PING_ENTITY_ID,
    N8N_STALE_WITHIN_SECONDS,
    STUCK_AFTER_SECONDS,
    _summarize_n8n_freshness,
)


UTC = timezone.utc
NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _summarize_n8n_freshness
# ---------------------------------------------------------------------------
def test_unknown_when_no_ping_ever():
    out = _summarize_n8n_freshness(last_ping_at=None, now=NOW)
    assert out["status"] == "unknown"
    assert out["last_ping_at"] is None
    assert out["seconds_since_last_ping"] is None


def test_healthy_when_recent():
    last = NOW - timedelta(seconds=30)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "healthy"
    assert out["seconds_since_last_ping"] == 30
    assert out["last_ping_at"] == last.isoformat()


def test_healthy_at_boundary():
    last = NOW - timedelta(seconds=N8N_HEALTHY_WITHIN_SECONDS)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "healthy"


def test_stale_just_past_healthy_boundary():
    last = NOW - timedelta(seconds=N8N_HEALTHY_WITHIN_SECONDS + 1)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "stale"


def test_stale_at_dead_boundary():
    last = NOW - timedelta(seconds=N8N_STALE_WITHIN_SECONDS)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "stale"


def test_dead_when_well_past_stale_window():
    last = NOW - timedelta(seconds=N8N_STALE_WITHIN_SECONDS + 1)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "dead"


def test_dead_for_very_old_ping():
    last = NOW - timedelta(hours=24)
    out = _summarize_n8n_freshness(last_ping_at=last, now=NOW)
    assert out["status"] == "dead"
    assert out["seconds_since_last_ping"] == 24 * 3600


# ---------------------------------------------------------------------------
# Constants sanity (catches accidental tuning regressions)
# ---------------------------------------------------------------------------
def test_thresholds_are_ordered():
    """Healthy window must be smaller than stale window, otherwise the
    classification logic short-circuits to 'healthy' for everything."""
    assert N8N_HEALTHY_WITHIN_SECONDS < N8N_STALE_WITHIN_SECONDS


def test_stuck_threshold_is_above_normal_turn_time():
    """STUCK_AFTER_SECONDS must be comfortably greater than a normal turn
    duration. With the new debounce: poll 2s + max-wait 15s + LLM ~3s + DB ~1s
    ≈ 21s ceiling. 60s is the absolute minimum; we use 120s for headroom."""
    assert STUCK_AFTER_SECONDS >= 60


def test_n8n_ping_entity_id_is_fixed():
    """The sentinel must be stable across deploys; otherwise queries that
    'WHERE entity_id = N8N_PING_ENTITY_ID' break."""
    import uuid
    assert N8N_PING_ENTITY_ID == uuid.UUID("00000000-0000-0000-0000-000000000031")
