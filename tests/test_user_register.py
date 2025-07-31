from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
import sys
from pathlib import Path
import importlib
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales_agent_api.app.models import Client, ClientUser


def setup_test_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def get_session_override():
        async with async_session() as session:
            yield session

    async def init_db_override():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    import sales_agent_api.app.db as db
    monkeypatch.setattr(db, "get_session", get_session_override)
    monkeypatch.setattr(db, "init_db", init_db_override)

    return engine, async_session


def create_app(monkeypatch):
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    monkeypatch.setenv("DBUSERNAME", "user")
    monkeypatch.setenv("DBPASSWORD", "pass")
    monkeypatch.setenv("DBHOST", "localhost")
    monkeypatch.setenv("DBNAME", "testdb")

    import sales_agent_api.app.db as db
    importlib.reload(db)

    engine, async_session = setup_test_db(monkeypatch)

    async def init_tables():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    asyncio.run(init_tables())

    import sales_agent_api.app.main as main
    importlib.reload(main)

    return main.app, engine, async_session


def test_register_user(monkeypatch):
    app, engine, async_session = create_app(monkeypatch)

    async def populate():
        async with async_session() as session:
            client = Client(id=1, name="cafe arenillo")
            session.add(client)
            await session.commit()

    asyncio.run(populate())

    client = TestClient(app)
    payload = {"name": "Bob", "phone_number": "987"}
    response = client.post("/users/register", json=payload)
    assert response.status_code == 201
    assert response.json() == {"message": "User registered successfully"}

    async def fetch_user():
        async with async_session() as session:
            result = await session.exec(select(ClientUser).where(ClientUser.phone_number == "987"))
            return result.first()

    user = asyncio.run(fetch_user())
    assert user is not None
    assert user.name == "Bob"
    assert user.client_id == 1
