"""
DATA ENGINE — Proxy Router
Reverse-proxies authenticated requests to downstream services.
"""

import os

import structlog
import httpx
from fastapi import APIRouter, Request, Response

from configs.settings import get_settings
from shared.authentication.jwt_handler import CurrentUser
from shared.exceptions.base import ServiceUnavailableError

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Proxy"])
settings = get_settings()

# In Docker each service is reachable via its Compose service name.
# Locally (python run.py) all services run on localhost.
# SERVICE_HOST env var lets the compose x-docker-env anchor override the host
# per service if needed; fallback to localhost for local dev.
def _svc_url(service_name: str, port: int) -> str:
    host = os.environ.get(f"SERVICE_HOST_{service_name.upper()}", "localhost")
    return f"http://{host}:{port}"


SERVICE_BASE_URLS = {
    "ingest":     _svc_url("ingestion_service",  settings.ingestion_service_port),
    "embed":      _svc_url("embedding_service",  settings.embedding_service_port),
    "vectors":    _svc_url("vector_vault",        settings.vector_vault_port),
    "telemetry":  _svc_url("telemetry_service",  settings.telemetry_service_port),
    "scrape":     _svc_url("trend_scraper",       settings.trend_scraper_port),
    "semantic":   _svc_url("semantic_engine",     settings.semantic_engine_port),
    "gaps":       _svc_url("gap_detection",       settings.gap_detection_port),
    "schema":     _svc_url("schema_factory",      settings.schema_factory_port),
    "dashboard":  _svc_url("dashboard_backend",   settings.dashboard_backend_port),
    "seo":        _svc_url("seo_engine",          settings.seo_engine_port),
    "geo":        _svc_url("geo_engine",          settings.geo_engine_port),
    "aeo":        _svc_url("aeo_engine",          settings.aeo_engine_port),
    # LEO: public inventory/pricing/availability API for direct AI engine consumption
    "leo":        _svc_url("geo_engine",          settings.geo_engine_port),
    # VSEO: multi-modal image/video optimiser endpoints
    "vseo":       _svc_url("geo_engine",          settings.geo_engine_port),
    # Synchronization service — handles sync runs and cache invalidation
    "sync":       _svc_url("synchronization",     settings.sync_service_port),
    "cache":      _svc_url("synchronization",     settings.sync_service_port),
}


async def _proxy(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    """
    Authenticated reverse proxy to downstream services.
    Injects tenant context header and forwards the request.
    """
    base_url = SERVICE_BASE_URLS.get(service)
    if not base_url:
        raise ServiceUnavailableError(f"Unknown service: '{service}'")

    target_url = f"{base_url}/api/v1/{service}/{path}"
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


# One decorated function per HTTP method gives FastAPI a unique operationId
# for each entry in the OpenAPI schema, eliminating the duplicate-ID warning.
@router.get("/{service}/{path:path}",    operation_id="proxy_get")
async def proxy_get(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    return await _proxy(service, path, request, current_user)

@router.post("/{service}/{path:path}",   operation_id="proxy_post")
async def proxy_post(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    return await _proxy(service, path, request, current_user)

@router.put("/{service}/{path:path}",    operation_id="proxy_put")
async def proxy_put(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    return await _proxy(service, path, request, current_user)

@router.delete("/{service}/{path:path}", operation_id="proxy_delete")
async def proxy_delete(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    return await _proxy(service, path, request, current_user)

@router.patch("/{service}/{path:path}",  operation_id="proxy_patch")
async def proxy_patch(service: str, path: str, request: Request, current_user: CurrentUser) -> Response:
    return await _proxy(service, path, request, current_user)
