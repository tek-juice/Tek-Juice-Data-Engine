"""
DATA ENGINE — Ingestion Service
Phase 1: FastAPI service for document upload, preprocessing, and pipeline dispatch.
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware

from configs.database import init_db, dispose_db
from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.tenant import TenantContextMiddleware
from services.ingestion_service.routers import ingest_router

settings = get_settings()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ingestion_service_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("ingestion_service_stopped")


app = FastAPI(
    title="DATA ENGINE — Ingestion Service",
    description="Document ingestion, preprocessing, and pipeline dispatch.",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url=None,
)

app.mount("/metrics", make_asgi_app())

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(AccessLogMiddleware)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.gateway_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception Handlers ────────────────────────────────────────────────────────
register_exception_handlers(app)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(ingest_router, prefix=API_PREFIX)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ingestion_service", "version": APP_VERSION}
