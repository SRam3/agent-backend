from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Load .env file located one level above this file
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _load_db_settings():
    """Load database credentials from Azure Key Vault or fallback to environment variables."""

    vault_url = os.getenv("KEY_VAULT_URL")
    if vault_url:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)

        username = client.get_secret("DBUSERNAME").value
        password = client.get_secret("DBPASSWORD").value  
        host = client.get_secret("DBHOST").value
        name = client.get_secret("DBNAME").value
    else:
        # local environment variable names
        username = os.getenv("DBUSERNAME")
        password = os.getenv("DBPASSWORD")
        host = os.getenv("DBHOST")
        name = os.getenv("DBNAME")

        if not all([username, password, host, name]):
            raise RuntimeError(
                "Database credentials not found. Provide KEY_VAULT_URL or set "
                "DBUSERNAME, DBPASSWORD, DBHOST, and DBNAME as environment variables."
            )

    return username, password, host, name


DBUSERNAME, DBPASSWORD, DBHOST, DBNAME = _load_db_settings()

# Build connection string
DATABASE_URL = f"postgresql+asyncpg://{DBUSERNAME}:{DBPASSWORD}@{DBHOST}/{DBNAME}"

# Async engine setup
engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=True, future=True)

# Initialize tables
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

# Get session
async def get_session() -> AsyncSession:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
