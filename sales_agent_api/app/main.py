from fastapi import FastAPI, Depends
from contextlib import asynccontextmanager
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from .db import init_db, get_session
from .models import ClientUser, Client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Welcome to the Sales Agent API!"}


@app.get("/health")
async def health_check():
    """Endpoint used by the LLM to verify connectivity with the backend."""
    return {"status": "ok", "message": "Backend reachable by LLM"}


@app.get("/users/by-phone/{phone_number}")
async def user_by_phone(
    phone_number: str, session: AsyncSession = Depends(get_session)
):
    """Lookup a client user by their WhatsApp phone number."""

    statement = (
        select(ClientUser)
        .join(Client)
        .where(Client.name == "cafe arenillo")
        .where(ClientUser.phone_number == phone_number)
    )
    result = await session.exec(statement)
    user = result.first()

    if user:
        return {
            "exists": True,
            "name": user.name,
            "user_id": user.id,
            "client_id": user.client_id,
        }

    return {
        "exists": False,
        "message": "Please ask the user for their name to continue.",
    }

