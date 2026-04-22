"""POST /api/v1/ingest/message — ingest an inbound WhatsApp message."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.ingest import (
    ClientNotFoundError,
    DuplicateMessageError,
    UserBlockedError,
    ingest_message,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class IngestMessageRequest(BaseModel):
    chakra_message_id: str
    phone_number: str
    content: str
    display_name: Optional[str] = None
    message_type: str = "text"
    timestamp: Optional[datetime] = None


class IngestMessageResponse(BaseModel):
    should_respond: bool
    conversation_id: uuid.UUID
    conversation_state: str
    strategy_directive: str
    strategy_meta: dict
    strategy_version: int
    client_config: dict
    user_context: dict
    product_catalog: list[dict] = []
    business_context: str = ""
    conversation_summary: str = ""
    recent_messages: list[dict]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/message", response_model=IngestMessageResponse)
async def ingest_message_endpoint(
    request: Request,
    body: IngestMessageRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestMessageResponse:
    """Process an inbound WhatsApp message.

    Requires:
      - Authorization: Bearer <SALES_AI_SERVICE_TOKEN>
      - X-Client-ID: <uuid of the tenant client>
    """
    client_id: uuid.UUID = request.state.client_id  # set by auth middleware

    try:
        result = await ingest_message(
            session=session,
            client_id=client_id,
            chakra_message_id=body.chakra_message_id,
            phone_number=body.phone_number,
            content=body.content,
            display_name=body.display_name,
            message_type=body.message_type,
            timestamp=body.timestamp,
        )
        await session.commit()
        return IngestMessageResponse(**result)

    except DuplicateMessageError:
        # Idempotent — return a minimal response without side effects
        await session.rollback()
        return IngestMessageResponse(
            should_respond=False,
            conversation_id=uuid.uuid4(),  # dummy — caller should check should_respond
            conversation_state="active",
            strategy_directive="",
            strategy_meta={},
            strategy_version=0,
            client_config={},
            user_context={},
            recent_messages=[],
        )

    except ClientNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except UserBlockedError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingest failed: {exc}",
        )
