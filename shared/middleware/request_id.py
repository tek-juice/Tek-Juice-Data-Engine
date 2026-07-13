"""
DATA ENGINE — Request ID Middleware
Attaches a unique request ID to every request for distributed tracing.
"""

import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from configs.constants import REQUEST_ID_HEADER

logger = structlog.get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique X-Request-ID to every incoming request.
    - Uses the incoming header value if present (for propagation).
    - Generates a new UUID if absent.
    - Adds the ID to the response header.
    - Binds the ID to structlog context for the duration of the request.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Store on request state for handlers to access
        request.state.request_id = request_id

        # Bind to structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
