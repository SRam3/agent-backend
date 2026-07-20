"""Tests for GET /health and auth middleware."""
import asyncio
import os
import sys

import pytest

# Ensure the sales_agent_api package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../sales_agent_api"))


def _reload_app(monkeypatch):
    """Helper: set env vars and reimport app.main cleanly."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test")
    monkeypatch.setenv("SALES_AI_SERVICE_TOKEN", "test-token-ci")
    monkeypatch.setenv("SALES_AI_OPERATOR_TOKEN", "test-operator-token-ci")
    monkeypatch.setenv("ENV", "dev")

    # Drop cached modules so env vars are picked up
    for mod in list(sys.modules.keys()):
        if mod.startswith("app"):
            del sys.modules[mod]

    from app.main import app  # noqa: F811
    return app


def test_health_returns_ok(monkeypatch):
    """GET /health → 200 {"status": "ok"}"""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    asyncio.run(_run())


def test_health_requires_no_auth(monkeypatch):
    """Health endpoint is public — no Authorization header needed."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200

    asyncio.run(_run())


def test_api_endpoint_returns_401_without_auth(monkeypatch):
    """POST /api/v1/ingest/message without auth header → 401."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/ingest/message", json={})
        assert response.status_code == 401

    asyncio.run(_run())


def test_api_endpoint_returns_401_with_wrong_token(monkeypatch):
    """POST /api/v1/ingest/message with wrong token → 401."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/ingest/message",
                json={},
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert response.status_code == 401

    asyncio.run(_run())


def test_api_endpoint_returns_400_without_client_id(monkeypatch):
    """Correct token but missing X-Client-ID → 400."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/ingest/message",
                json={},
                headers={"Authorization": "Bearer test-token-ci"},
            )
        assert response.status_code == 400

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Operator surface token scoping (ADR-009): two surfaces, two keys.
# All cases below are rejected by the middleware — no DB is touched.
# ---------------------------------------------------------------------------

def test_operator_endpoint_rejects_service_token(monkeypatch):
    """The generic service token must NOT open the confirm-payment surface."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/operator/confirm-payment",
                json={},
                headers={"Authorization": "Bearer test-token-ci"},
            )
        assert response.status_code == 401

    asyncio.run(_run())


def test_agent_endpoint_rejects_operator_token(monkeypatch):
    """The operator token must NOT open the agent/ingest surfaces."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/agent/action",
                json={},
                headers={"Authorization": "Bearer test-operator-token-ci"},
            )
        assert response.status_code == 401

    asyncio.run(_run())


def test_operator_endpoint_rejects_missing_auth(monkeypatch):
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    application = _reload_app(monkeypatch)

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/operator/confirm-payment", json={})
        assert response.status_code == 401

    asyncio.run(_run())


def test_operator_endpoint_500_when_token_unconfigured(monkeypatch):
    """Fail closed: with no operator token configured, the surface is unusable
    (500), never falls back to the service token."""
    from httpx import AsyncClient
    from httpx._transports.asgi import ASGITransport

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test")
    monkeypatch.setenv("SALES_AI_SERVICE_TOKEN", "test-token-ci")
    monkeypatch.delenv("SALES_AI_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("ENV", "dev")
    for mod in list(sys.modules.keys()):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app.main import app as application

    async def _run():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/operator/confirm-payment",
                json={},
                headers={"Authorization": "Bearer test-token-ci"},
            )
        assert response.status_code == 500

    asyncio.run(_run())
