"""POST /api/v1/agent/action — how a stale turn reaches n8n (INV-CONV-003).

The service raises StaleContextError; this checks the HTTP contract on top of
it. P5 depends on n8n telling a 409 `stale_context` apart from a backend that
is down, so the status code and the error key are the contract, not details.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../sales_agent_api"))

from tests.test_health import _reload_app

_CLIENT_ID = "00000000-0000-0000-0000-000000000001"


class _Session:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def test_a_stale_turn_answers_409_stale_context_and_rolls_back(monkeypatch):
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    import app.api.v1.agent as agent_module
    from app.core.database import get_session
    from app.services.agent_action import StaleContextError

    async def _stale(**_kwargs):
        raise StaleContextError("strategy_version mismatch: expected 4, got 3")

    monkeypatch.setattr(agent_module, "process_agent_action", _stale)
    session = _Session()

    async def _session_override():
        yield session

    application.dependency_overrides[get_session] = _session_override

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/agent/action",
                headers={
                    "Authorization": "Bearer test-token-ci",
                    "X-Client-ID": _CLIENT_ID,
                },
                json={
                    "conversation_id": str(uuid.uuid4()),
                    "strategy_version": 3,
                    "response_text": "Hola",
                },
            )

    response = asyncio.run(_run())

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "stale_context"
    assert session.rolled_back is True
    assert session.committed is False
