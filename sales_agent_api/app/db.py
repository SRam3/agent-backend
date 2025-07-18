from sqlmodel import SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

load_dotenv()


def _load_db_settings():
    """Load database credentials from Azure Key Vault."""

    vault_url = os.getenv("KEY_VAULT_URL")
    if not vault_url:
        raise RuntimeError("KEY_VAULT_URL environment variable must be set")

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)

    username = client.get_secret("DBUSERNAME").value
    password = client.get_secret("psqladmin-password").value
    host = client.get_secret("DBHOST").value
    name = client.get_secret("DBNAME").value

    return username, password, host, name

DB_USERNAME, DB_PASSWORD, DB_HOST, DB_NAME = _load_db_settings()

# Construct DATABASE_URL
DATABASE_URL = (
    f"postgresql+asyncpg://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
)

engine: AsyncEngine = create_engine(DATABASE_URL, echo=True, future=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
