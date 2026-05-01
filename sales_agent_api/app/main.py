"""FastAPI application factory.

Authentication:
  - Service-to-service Bearer token (SALES_AI_SERVICE_TOKEN env var)
  - Constant-time comparison to prevent timing attacks

Tenant identification:
  - X-Client-ID header (UUID) — injected into request.state.client_id

Docs:
  - Enabled when ENV != "production"
"""
from __future__ import annotations

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import ping_db

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

ENV = os.getenv("ENV", "dev")
SERVICE_TOKEN = os.getenv("SALES_AI_SERVICE_TOKEN", "")

# Endpoints that bypass auth
_NO_AUTH_PATHS = {"/health", "/", "/api/docs", "/openapi.json"}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
def _bootstrap_openai_key() -> None:
    """Resolve OPENAI_API_KEY from Key Vault on boot if not already set.

    Order:
      1. OPENAI_API_KEY env var (local dev / CI)
      2. Azure Key Vault secret named 'openai-key' (production)

    No-op if neither is available — conversation_summary.py degrades gracefully
    (logs a warning and returns None instead of summarising).
    """
    if os.getenv("OPENAI_API_KEY"):
        logger.info("OpenAI key: loaded from environment")
        return

    kv_url = os.getenv("KEY_VAULT_URL")
    if not kv_url:
        logger.warning("OpenAI key: not set, KEY_VAULT_URL absent — summarisation disabled")
        return

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential(exclude_shared_token_cache_credential=True)
        kv = SecretClient(vault_url=kv_url, credential=credential)
        os.environ["OPENAI_API_KEY"] = kv.get_secret("openai-key").value
        logger.info("OpenAI key: loaded from Key Vault (openai-key)")
    except Exception as exc:
        logger.warning("OpenAI key: failed to load from Key Vault: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_openai_key()
    ok = await ping_db()
    if ok:
        logger.info("Database connection: OK")
    else:
        logger.warning("Database connection: FAILED — check DATABASE_URL")
    yield


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    docs_url = "/api/docs" if ENV != "production" else None
    openapi_url = "/openapi.json" if ENV != "production" else None

    application = FastAPI(
        title="Sales AI Agent Backend",
        version="1.0.0",
        docs_url=docs_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # Auth + tenant middleware
    @application.middleware("http")
    async def auth_and_tenant_middleware(request: Request, call_next):
        path = request.url.path

        # Skip auth for health and docs
        if path in _NO_AUTH_PATHS or path.startswith("/api/docs"):
            return await call_next(request)

        # --- Bearer token check ---
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _unauthorized("Missing or invalid Authorization header")

        token = auth_header[len("Bearer "):]
        if not SERVICE_TOKEN:
            logger.error("SALES_AI_SERVICE_TOKEN is not configured")
            return _server_error("Service token not configured")

        if not hmac.compare_digest(token.encode(), SERVICE_TOKEN.encode()):
            return _unauthorized("Invalid service token")

        # --- X-Client-ID header ---
        client_id_header = request.headers.get("X-Client-ID", "")
        if not client_id_header:
            return _bad_request("X-Client-ID header is required")

        try:
            client_id = uuid.UUID(client_id_header)
        except ValueError:
            return _bad_request("X-Client-ID must be a valid UUID")

        request.state.client_id = client_id
        return await call_next(request)

    # Register routers
    from app.api.v1.ingest import router as ingest_router
    from app.api.v1.agent import router as agent_router

    application.include_router(ingest_router, prefix="/api/v1/ingest", tags=["Ingest"])
    application.include_router(agent_router, prefix="/api/v1/agent", tags=["Agent"])

    @application.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    async def root():
        return {"service": "Sales AI Agent Backend", "status": "ok"}

    return application


app = create_app()


# ---------------------------------------------------------------------------
# Helper response builders (used in middleware — can't use HTTPException there)
# ---------------------------------------------------------------------------
from fastapi.responses import JSONResponse


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": detail},
    )


def _server_error(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": detail},
    )
