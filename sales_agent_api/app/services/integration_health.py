"""Integration health: n8n heartbeat + stuck-conversation detector.

Why this exists: prior to this service, if the n8n cafe_arenillo_v2
subworkflow stopped firing (ID changed, trigger broken, container down),
the backend had no way to know — the only signal was customer messages
not getting answered. Documented as deuda #3 in CLAUDE.md and as
limitation L3 in the README ("Fallo silencioso del subworkflow").

Two health signals exposed:

1. n8n freshness — n8n posts to /api/v1/internal/n8n-ping every 60s. We
   record it as an audit_log event and expose how stale the latest one is.

2. Stuck conversations — for each client, count conversations whose
   latest message is inbound (we owe a reply) and where last_message_at
   is older than STUCK_AFTER_SECONDS. A non-zero count means either n8n
   isn't delivering the ingest, or backend rejected the agent_action,
   or some mid-flow failure. Either way, customers are waiting.

Both signals are scoped per client_id (multi-tenant), so a future operator
dashboard can show per-tenant integration status.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditLog, Conversation, Message

logger = logging.getLogger(__name__)


# Tunables. STUCK_AFTER_SECONDS is generous on purpose — under the new
# rearmable debounce, normal turns can take up to 15s, so 120s gives plenty
# of headroom before flagging as stuck.
STUCK_AFTER_SECONDS = 120
N8N_HEALTHY_WITHIN_SECONDS = 180   # < 3 min since last ping → healthy
N8N_STALE_WITHIN_SECONDS = 600     # 3-10 min → stale; > 10 min → dead

# Sentinel UUID used as the entity_id for every n8n_ping audit_log row.
# A fixed value (instead of a fresh uuid4 per ping) keeps the audit trail
# scannable: WHERE entity_id = N8N_PING_ENTITY_ID lists the full heartbeat
# history without joining anything else.
N8N_PING_ENTITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000031")


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
async def record_n8n_ping(
    session: AsyncSession,
    client_id: uuid.UUID,
    workflow_name: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> None:
    """Append a heartbeat row to audit_log. Caller controls commit."""
    payload = {"workflow_name": workflow_name, "execution_id": execution_id}
    session.add(
        AuditLog(
            client_id=client_id,
            event_type="n8n_ping",
            entity_type="n8n_workflow",
            entity_id=N8N_PING_ENTITY_ID,
            actor_type="system",
            new_value={k: v for k, v in payload.items() if v is not None},
        )
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def get_integration_health(
    session: AsyncSession,
    client_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> dict:
    """Build the integration-health snapshot for one client."""
    now = now or datetime.now(timezone.utc)

    last_ping_row = await session.execute(
        select(func.max(AuditLog.created_at)).where(
            AuditLog.client_id == client_id,
            AuditLog.event_type == "n8n_ping",
        )
    )
    last_ping_at: Optional[datetime] = last_ping_row.scalar_one_or_none()

    stuck = await _count_stuck_conversations(session, client_id, now=now)

    return {
        "n8n": _summarize_n8n_freshness(last_ping_at, now=now),
        "stuck_conversations": stuck,
        "checked_at": now.isoformat(),
    }


def _summarize_n8n_freshness(
    last_ping_at: Optional[datetime],
    now: datetime,
) -> dict:
    """Pure function: classify n8n ping freshness."""
    if last_ping_at is None:
        return {"status": "unknown", "last_ping_at": None, "seconds_since_last_ping": None}
    seconds = int((now - last_ping_at).total_seconds())
    if seconds <= N8N_HEALTHY_WITHIN_SECONDS:
        status = "healthy"
    elif seconds <= N8N_STALE_WITHIN_SECONDS:
        status = "stale"
    else:
        status = "dead"
    return {
        "status": status,
        "last_ping_at": last_ping_at.isoformat(),
        "seconds_since_last_ping": seconds,
    }


async def _count_stuck_conversations(
    session: AsyncSession,
    client_id: uuid.UUID,
    now: datetime,
    stuck_after_seconds: int = STUCK_AFTER_SECONDS,
) -> dict:
    """Conversations where the latest message is inbound and old enough
    that we should have answered by now."""
    threshold = now - timedelta(seconds=stuck_after_seconds)

    # For each conversation in this client, find the latest message and check
    # if it is inbound + older than threshold.
    latest_per_conv = (
        select(
            Message.conversation_id.label("cid"),
            func.max(Message.created_at).label("latest_ts"),
        )
        .where(Message.client_id == client_id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    stuck_q = (
        select(Conversation.id, Message.created_at)
        .join(latest_per_conv, latest_per_conv.c.cid == Conversation.id)
        .join(
            Message,
            and_(
                Message.conversation_id == Conversation.id,
                Message.created_at == latest_per_conv.c.latest_ts,
            ),
        )
        .where(
            Conversation.client_id == client_id,
            Conversation.state.in_(("active", "human_handoff")),
            Message.direction == "inbound",
            Message.created_at < threshold,
        )
        .order_by(Message.created_at.asc())
        .limit(10)
    )
    rows = (await session.execute(stuck_q)).all()
    examples = [
        {
            "conversation_id": str(cid),
            "last_inbound_at": ts.isoformat(),
            "minutes_pending": int((now - ts).total_seconds() // 60),
        }
        for cid, ts in rows
    ]
    return {"count": len(examples), "examples": examples}
