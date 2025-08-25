from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from .db import init_db, get_session
from .models import ClientUser


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

class UserLookupResponse(BaseModel):
    exists: bool
    user_id: UUID | None = None
    client_id: UUID | None = None
    name: str | None = None
    message: str | None = None

@app.get("/")
async def root():
    return {"message": "Welcome to the Sales Agent API!"}


@app.get("/health")
async def health_check():
    """Endpoint used by the LLM to verify connectivity with the backend."""
    return {"status": "ok", "message": "Backend reachable by LLM"}



