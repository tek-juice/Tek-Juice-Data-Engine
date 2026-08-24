"""
DATA ENGINE — Access Logging Middleware
Structured JSON access logs for every HTTP request/response.
"""

import time
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger("data_engine.access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    """
    Logs every request with:
    - Method, path, status code
    - Duration in milliseconds
    - Request ID and tenant ID (from context)
    - Client IP
    """

    # Paths to skip logging (reduces noise)
    _SKIP_PATHS = {"/health", "/metrics"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query) if request.url.query else None,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        response.headers["X-Response-Time"] = f"{duration_ms}ms"
        return response
