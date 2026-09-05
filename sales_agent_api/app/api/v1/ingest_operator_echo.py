"""POST /api/v1/ingest/operator-echo — ingest a message the OPERATOR sent.

Its own surface, not a branch of `POST /ingest/message`, and that is
architectural rather than cosmetic (ADR-013 §2): an echo is not a turn, so it
must not reach the debounce, the strategy engine or the LLM. Threading a large
conditional through the most complex function in the system is exactly how
deuda #2 got there.

The second reason matters more over time: **the invariant becomes structural.**
"No checkpoint may be marked from an echo" holds because this surface has no
ability to write one.

Auth: the SERVICE token, via the same middleware as the rest of ingest. NOT the
operator token — `SALES_AI_OPERATOR_TOKEN` is path-scoped to `/api/v1/operator/*`
and this is not an operator ACTION, it is webhook ingestion. Mounting the route
under the ingest prefix is what makes that distinction free of new auth code.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.ingest_operator_echo import (
    ClientNotFoundError,
    DuplicateMessageError,
    ingest_operator_echo,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class OperatorEchoRequest(BaseModel):
    """One entry of `message_echoes[]`, normalised by n8n.

    Identity comes from the echo's `to_user_id` (the RECIPIENT — the customer),
    falling back to `contacts[0].user_id`. Measured on prod: `contacts[0].user_id`
    is present in 247 of 247 payloads, and the whitelist node already copies it
    since P14, so the BSUID survives an echo intact.

    `phone_number` stays accepted for the pre-privacy shape, and at least one of
    the two identities must arrive — same contract as the inbound ingest.
    """

    chakra_message_id: str
    bsuid: Optional[str] = None
    phone_number: Optional[str] = None
    content: str
    display_name: Optional[str] = None
    message_type: str = "text"
    #: Meta's clock, not ours. See ADR-011 §5.2.4 and the service's step 5.
    timestamp: Optional[datetime] = None

    @model_validator(mode="after")
    def _require_some_identity(self) -> "OperatorEchoRequest":
        if not self.bsuid and not self.phone_number:
            raise ValueError("at least one of 'bsuid' or 'phone_number' is required")
        return self


class OperatorEchoResponse(BaseModel):
    #: "ingested" or "duplicate". Never an error for a redelivery.
    status: str
    conversation_id: Optional[uuid.UUID] = None
    message_id: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/operator-echo", response_model=OperatorEchoResponse)
async def ingest_operator_echo_endpoint(
    request: Request,
    body: OperatorEchoRequest,
    session: AsyncSession = Depends(get_session),
) -> OperatorEchoResponse:
    """Persist a message the operator sent to the customer.

    Requires:
      - Authorization: Bearer <SALES_AI_SERVICE_TOKEN>
      - X-Client-ID: <uuid of the tenant client>

    Never computes a strategy, never calls the LLM, never marks anything.
    """
    client_id: uuid.UUID = request.state.client_id  # set by auth middleware

    try:
        result = await ingest_operator_echo(
            session=session,
            client_id=client_id,
            chakra_message_id=body.chakra_message_id,
            bsuid=body.bsuid,
            phone_number=body.phone_number,
            content=body.content,
            display_name=body.display_name,
            message_type=body.message_type,
            timestamp=body.timestamp,
        )
        await session.commit()
        return OperatorEchoResponse(**result)

    except DuplicateMessageError:
        # Meta redelivered. Idempotent by the UNIQUE index, so there is nothing
        # to write and nothing to report as broken. 200, not 409.
        await session.rollback()
        return OperatorEchoResponse(status="duplicate")

    except ClientNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Operator echo ingest failed: {exc}",
        )
