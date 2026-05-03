"""Internal observability endpoints.

  POST /api/v1/internal/n8n-ping        — heartbeat from n8n every ~60s
  GET  /api/v1/internal/integration-health — read the freshness + stuck count

Auth: same Bearer + X-Client-ID as the rest of /api/v1. n8n includes the
client_id of the subworkflow whose health is being reported (Café Arenillo
today; future tenants will identify themselves the same way).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.integration_health import (
    get_integration_health,
    record_n8n_ping,
)

router = APIRouter()


class N8nPingRequest(BaseModel):
    workflow_name: Optional[str] = None
    execution_id: Optional[str] = None


class N8nPingResponse(BaseModel):
    recorded: bool


@router.post("/n8n-ping", response_model=N8nPingResponse)
async def n8n_ping(
    request: Request,
    body: N8nPingRequest,
    session: AsyncSession = Depends(get_session),
) -> N8nPingResponse:
    """Append a heartbeat row to audit_log and commit.

    Idempotency is not required — duplicate pings are harmless extra rows.
    """
    client_id: uuid.UUID = request.state.client_id
    try:
        await record_n8n_ping(
            session=session,
            client_id=client_id,
            workflow_name=body.workflow_name,
            execution_id=body.execution_id,
        )
        await session.commit()
        return N8nPingResponse(recorded=True)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"n8n_ping persistence failed: {exc}",
        )


@router.get("/integration-health")
async def integration_health(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Snapshot of n8n freshness + stuck conversations for this client.

    No commit — read-only.
    """
    client_id: uuid.UUID = request.state.client_id
    try:
        return await get_integration_health(session=session, client_id=client_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"integration_health query failed: {exc}",
        )
