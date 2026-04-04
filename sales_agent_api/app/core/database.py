"""Async SQLAlchemy engine and session factory.

Connection string resolution order:
  1. DATABASE_URL env var (direct connection string)
  2. KEY_VAULT_URL → fetch secrets from Azure Key Vault
  3. Individual env vars: DBUSERNAME / DBPASSWORD / DBHOST / DBNAME
"""
from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

load_dotenv()

logger = logging.getLogger(__name__)


def _build_database_url() -> str:
    # Option 1: direct DATABASE_URL
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # Option 2: Azure Key Vault
    kv_url = os.getenv("KEY_VAULT_URL")
    if kv_url:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        kv = SecretClient(vault_url=kv_url, credential=credential)
        db_user = kv.get_secret("DBUSERNAME").value
        db_pass = kv.get_secret("DBPASSWORD").value
        db_host = kv.get_secret("DBHOST").value
        db_name = kv.get_secret("DBNAME").value
        db_port = os.getenv("DBPORT", "5432")
        return f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}?sslmode=require"

    # Option 3: individual env vars
    db_user = os.getenv("DBUSERNAME")
    db_pass = os.getenv("DBPASSWORD")
    db_host = os.getenv("DBHOST", "localhost")
    db_name = os.getenv("DBNAME", "postgres")
    db_port = os.getenv("DBPORT", "5432")

    if db_user and db_pass:
        return f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}?sslmode=require"

    raise RuntimeError(
        "No database configuration found. Set DATABASE_URL, KEY_VAULT_URL, "
        "or DBUSERNAME/DBPASSWORD/DBHOST/DBNAME environment variables."
    )


DATABASE_URL = _build_database_url()

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("ENV", "dev") == "dev",
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def ping_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database ping failed: %s", exc)
        return False
