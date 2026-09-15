"""
DATA ENGINE — API Gateway
Phase 3: Central entry point for all external API traffic.
Handles authentication, rate limiting, routing, and health aggregation.
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION, APP_NAME
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.tenant import TenantContextMiddleware
from services.api_gateway.routers.auth_router import router as auth_router
from services.api_gateway.routers.proxy_router import router as proxy_router
from services.api_gateway.routers.health_router import router as health_router
from services.api_gateway.routers.onboard_router import router as onboard_router
from services.api_gateway.routers.sdk_router import router as sdk_router

settings = get_settings()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("api_gateway_starting", version=APP_VERSION)
    await init_db()   # schedules background task internally; returns immediately
    yield
    await dispose_db()
    logger.info("api_gateway_stopped")


# Read settings once here — at module load time these values are fine
# because they don't affect DB connection (only CORS origins and docs URL)
_settings = get_settings()

app = FastAPI(
    title=f"{APP_NAME} — API Gateway",
    description="Central API Gateway — authentication, routing, rate limiting.",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _settings.is_development else None,
    redoc_url=None,
)

# ── Prometheus metrics endpoint ───────────────────────────────────────────────
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(AccessLogMiddleware)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.gateway_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time"],
)

# ── Exception Handlers ────────────────────────────────────────────────────────
register_exception_handlers(app)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(auth_router,    prefix=f"{API_PREFIX}/auth")
app.include_router(proxy_router,   prefix=API_PREFIX)
# ── Self-service onboarding (no auth required) ────────────────────────────────
app.include_router(onboard_router)
app.include_router(sdk_router)
