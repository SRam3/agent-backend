"""POST /api/v1/operator/confirm-payment — operator closes a sale (ADR-009).

Operator surface: authenticated with SALES_AI_OPERATOR_TOKEN (path-scoped in
the auth middleware — the generic service token is NOT accepted here). This
endpoint is, in effect, the "a sale was paid" button.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.confirm_payment import (
    ConfirmPaymentError,
    ConversationNotFoundError,
    OrderNotConfirmedError,
    confirm_payment,
)
from app.services.state_machine import StateMachineError

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ConfirmPaymentRequest(BaseModel):
    conversation_id: uuid.UUID


class ConfirmPaymentResponse(BaseModel):
    confirmed: bool
    already_confirmed: bool
    new_state: str
    side_effects: list[str]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/confirm-payment", response_model=ConfirmPaymentResponse)
async def confirm_payment_endpoint(
    request: Request,
    body: ConfirmPaymentRequest,
    session: AsyncSession = Depends(get_session),
) -> ConfirmPaymentResponse:
    """Mark a sale as paid after the operator reviewed the receipt.

    Requires:
      - Authorization: Bearer <SALES_AI_OPERATOR_TOKEN>
      - X-Client-ID: <uuid of the tenant client>

    Semantics:
      - Idempotent: re-confirming an already-closed sale returns 200 with
        already_confirmed=true and does NOT duplicate the purchase record.
      - Strict precondition: 409 if the customer never confirmed the order
        (no user_confirmation in context).
    """
    client_id: uuid.UUID = request.state.client_id  # set by auth middleware

    try:
        result = await confirm_payment(
            session=session,
            client_id=client_id,
            conversation_id=body.conversation_id,
        )
        await session.commit()
        return ConfirmPaymentResponse(confirmed=True, **result)

    except ConversationNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    except OrderNotConfirmedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "order_not_confirmed", "message": str(exc)},
        )

    except StateMachineError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "invalid_state", "message": str(exc)},
        )

    except ConfirmPaymentError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Confirm payment failed: {exc}",
        )
