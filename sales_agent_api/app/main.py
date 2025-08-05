from fastapi import FastAPI, Depends, status
from pydantic import BaseModel
from contextlib import asynccontextmanager
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from .db import init_db, get_session
from .models import ClientUser, Client


class UserRegisterRequest(BaseModel):
    name: str
    phone_number: str

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


@app.post("/users/register", status_code=status.HTTP_201_CREATED)
async def register_user(
    user: UserRegisterRequest, session: AsyncSession = Depends(get_session)
):
    """Register a new user for the cafe arenillo client."""

    # 1. Fetch the cafe client or create it if missing
    statement = select(Client).where(Client.name == "cafe arenillo")
    result = await session.exec(statement)
    client = result.first()
    if not client:
        client = Client(name="cafe arenillo")
        session.add(client)
        await session.commit()
        await session.refresh(client)

    # 2. Check if user already exists (by phone + client)
    statement = select(ClientUser).where(
        ClientUser.phone == user.phone,
        ClientUser.client_id == client.id
    )
    result = await session.exec(statement)
    existing_user = result.first()
    if existing_user:
        return {"message": "User already registered", "user_id": existing_user.id}

    # 3. Otherwise, create user
    new_user = ClientUser(
        name=user.name, phone=user.phone, client_id=client.id
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)
    return {"message": "User registered successfully", "user_id": new_user.id}

