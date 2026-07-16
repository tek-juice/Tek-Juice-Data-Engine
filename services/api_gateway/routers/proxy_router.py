"""
DATA ENGINE — Proxy Router
Reverse-proxies authenticated requests to downstream services.
"""

import structlog
import httpx
from fastapi import APIRouter, Request, Response

from configs.settings import get_settings
from shared.authentication.jwt_handler import CurrentUser
from shared.exceptions.base import ServiceUnavailableError

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Proxy"])
settings = get_settings()

SERVICE_BASE_URLS = {
    "ingest":     f"http://localhost:{settings.ingestion_service_port}",
    "embed":      f"http://localhost:{settings.embedding_service_port}",
    "vectors":    f"http://localhost:{settings.vector_vault_port}",
    "telemetry":  f"http://localhost:{settings.telemetry_service_port}",
    "scrape":     f"http://localhost:{settings.trend_scraper_port}",
    "semantic":   f"http://localhost:{settings.semantic_engine_port}",
    "gaps":       f"http://localhost:{settings.gap_detection_port}",
    "schema":     f"http://localhost:{settings.schema_factory_port}",
    "dashboard":  f"http://localhost:{settings.dashboard_backend_port}",
    "seo":        f"http://localhost:{settings.seo_engine_port}",
    "geo":        f"http://localhost:{settings.geo_engine_port}",
    "aeo":        f"http://localhost:{settings.aeo_engine_port}",
    # LEO: public inventory/pricing/availability API for direct AI engine consumption
    "leo":        f"http://localhost:{settings.geo_engine_port}",
    # VSEO: multi-modal image/video optimiser endpoints
    "vseo":       f"http://localhost:{settings.geo_engine_port}",
}


@router.api_route("/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(
    service: str,
    path: str,
    request: Request,
    current_user: CurrentUser,
):
    """
    Authenticated reverse proxy to downstream services.
    Injects tenant context header and forwards the request.
    """
    base_url = SERVICE_BASE_URLS.get(service)
    if not base_url:
        raise ServiceUnavailableError(f"Unknown service: '{service}'")

    target_url = f"{base_url}/api/v1/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    headers["X-Tenant-ID"] = current_user.tenant_id
    headers["X-User-ID"] = current_user.sub
    headers["X-User-Role"] = current_user.role

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=dict(upstream.headers),
                media_type=upstream.headers.get("content-type"),
            )
    except httpx.ConnectError:
        raise ServiceUnavailableError(f"Service '{service}' is unreachable.")
    except httpx.TimeoutException:
        raise ServiceUnavailableError(f"Service '{service}' timed out.")
