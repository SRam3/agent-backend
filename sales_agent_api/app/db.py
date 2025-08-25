from pathlib import Path
import os

from dotenv import load_dotenv
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.orm import sessionmaker

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


KEY_VAULT_URL = os.getenv("KEY_VAULT_URL")
if not KEY_VAULT_URL:
    raise RuntimeError("KEY_VAULT_URL must be set in .env or container env vars")

# Connect to Key Vault and fetch secrets
credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

DBUSER = client.get_secret("DBUSERNAME").value
DBPASS = client.get_secret("DBPASSWORD").value
DBHOST = client.get_secret("DBHOST").value
DBNAME = client.get_secret("DBNAME").value
DBPORT = os.getenv("DBPORT", "5432")  # optional, default 5432

# Azure enforces TLS by default → sslmode=require
DATABASE_URL = (
    f"postgresql+asyncpg://{DBUSER}:{DBPASS}@{DBHOST}:{DBPORT}/{DBNAME}"
    "?sslmode=require"
)

# Async engine and session factory
engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=True, future=True)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def init_db() -> None:
    """Create tables from SQLModel metadata (only for prototyping)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

async def ping_db() -> bool:
    try:
        async with engine.begin() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:
        return False
