"""
DATA ENGINE — Dashboard Backend API
Phase 3: Central Command — admin dashboard, telemetry stream viewer,
before/after gap graph, and real-time WebSocket activity feed.
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from configs.database import init_db, dispose_db
from configs.settings import get_settings
from configs.constants import APP_VERSION, API_PREFIX
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.tenant import TenantContextMiddleware
from services.dashboard_backend.analytics.analytics_router import router as analytics_router
from services.dashboard_backend.metrics.metrics_router import router as metrics_router
from services.dashboard_backend.websocket.ws_router import router as ws_router

settings = get_settings()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("dashboard_backend_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("dashboard_backend_stopped")


app = FastAPI(
    title="DATA ENGINE — Dashboard Backend",
    description="Admin dashboard API — analytics, metrics, gap graphs, and live activity feed.",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url=None,
)

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

register_exception_handlers(app)

app.include_router(analytics_router, prefix=f"{API_PREFIX}/dashboard")
app.include_router(metrics_router,   prefix=f"{API_PREFIX}/dashboard")
app.include_router(ws_router,        prefix="/ws")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "dashboard_backend", "version": APP_VERSION}
