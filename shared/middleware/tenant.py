"""
DATA ENGINE — Tenant Context Middleware
Extracts tenant ID from the JWT/API key and sets the PostgreSQL
row-level security context variable for the current transaction.
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from configs.constants import TENANT_ID_HEADER

logger = structlog.get_logger(__name__)

# Paths that don't require tenant context (auth, health, docs, onboarding)
_EXEMPT_PATHS = {
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/auth/token",
    "/auth/refresh",
    # Self-service onboarding — no tenant exists yet when these are called
    "/connect",
    "/onboard",
    "/onboard/verify-email",
    "/onboard/scan",
    "/onboard/ping",
    "/onboard/install",
    "/sdk.js",
    "/sdk/signal",
}


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Reads tenant_id from:
    1. request.state.tenant_id — set by the auth dependency after JWT decode
    2. X-Tenant-ID header — for service-to-service calls with pre-validated identity

    Binds tenant_id to structlog context and request state.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # Prefer tenant_id already set by auth dependency
        tenant_id = getattr(request.state, "tenant_id", None)

        # Fall back to header (service-to-service)
        if not tenant_id:
            tenant_id = request.headers.get(TENANT_ID_HEADER)

        if tenant_id:
            structlog.contextvars.bind_contextvars(tenant_id=str(tenant_id))
            request.state.tenant_id = tenant_id

        response = await call_next(request)
        return response
