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


def _build_database_url() -> str:
    """Determine the database URL.

    Preference order:
    1. Azure Key Vault secrets if ``KEY_VAULT_URL`` is provided.
    2. Environment variables (``DBUSERNAME`` etc.).
    3. Local SQLite file for simple development setups.
    """

    vault_url = os.getenv("KEY_VAULT_URL")

    if vault_url:
        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=vault_url, credential=credential)
            username = client.get_secret("DBUSERNAME").value
            password = client.get_secret("DBPASSWORD").value
            host = client.get_secret("DBHOST").value
            name = client.get_secret("DBNAME").value
            return f"postgresql+asyncpg://{username}:{password}@{host}/{name}"
        except Exception:
            # Fall back to other sources
            pass

    username = os.getenv("DBUSERNAME")
    password = os.getenv("DBPASSWORD")
    host = os.getenv("DBHOST")
    name = os.getenv("DBNAME")
    if all([username, password, host, name]):
        return f"postgresql+asyncpg://{username}:{password}@{host}/{name}"

    # Fallback to a local SQLite database so the API can function without
    # external configuration.  The file is created in the current working
    # directory if it does not exist.
    return "sqlite+aiosqlite:///./sales_agent.db"


DATABASE_URL = _build_database_url()

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
