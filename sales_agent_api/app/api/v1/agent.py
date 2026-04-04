"""POST /api/v1/agent/action — validate and execute an agent proposal."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.agent_action import (
    AgentActionError,
    ConversationNotFoundError,
    StaleContextError,
    process_agent_action,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class AgentActionRequest(BaseModel):
    conversation_id: uuid.UUID
    strategy_version: int
    response_text: str
    proposed_action: Optional[str] = None
    proposed_transition: Optional[str] = None
    extracted_data: Optional[dict] = None
    ai_model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None


class AgentActionResponse(BaseModel):
    approved: bool
    final_response_text: str
    new_state: str
    side_effects: list[str]
    rejection_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/action", response_model=AgentActionResponse)
async def agent_action_endpoint(
    request: Request,
    body: AgentActionRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentActionResponse:
    """Validate and execute an agent proposal.

    Requires:
      - Authorization: Bearer <SALES_AI_SERVICE_TOKEN>
      - X-Client-ID: <uuid of the tenant client>

    Key behaviours:
      - If the action is rejected, the response_text still reaches the user.
      - If strategy_version is stale (conversation changed between Call 1 and Call 2),
        returns HTTP 409 Conflict.
    """
    client_id: uuid.UUID = request.state.client_id  # set by auth middleware

    try:
        result = await process_agent_action(
            session=session,
            client_id=client_id,
            conversation_id=body.conversation_id,
            strategy_version=body.strategy_version,
            response_text=body.response_text,
            proposed_action=body.proposed_action,
            proposed_transition=body.proposed_transition,
            extracted_data=body.extracted_data,
            ai_model=body.ai_model,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            latency_ms=body.latency_ms,
        )
        await session.commit()
        return AgentActionResponse(**result)

    except StaleContextError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "stale_context", "message": str(exc)},
        )

    except ConversationNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except AgentActionError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent action failed: {exc}",
        )
