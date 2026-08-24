"""
DATA ENGINE — Health Router
Aggregates health checks across all registered services.
Phase 3: Central Command monitoring interface.
"""

import asyncio
import os

import structlog
import httpx
from fastapi import APIRouter
from configs.database import check_db_health
from configs.settings import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Health"])
settings = get_settings()


def _svc_health_url(service_name: str, port: int) -> str:
    """Build a service health URL, respecting Docker service host overrides."""
    host = os.environ.get(f"SERVICE_HOST_{service_name.upper()}", "localhost")
    return f"http://{host}:{port}/health"


SERVICE_HEALTH_URLS = {
    "ingestion_service":   _svc_health_url("ingestion_service",  settings.ingestion_service_port),
    "embedding_service":   _svc_health_url("embedding_service",  settings.embedding_service_port),
    "vector_vault":        _svc_health_url("vector_vault",        settings.vector_vault_port),
    "telemetry_service":   _svc_health_url("telemetry_service",  settings.telemetry_service_port),
    "trend_scraper":       _svc_health_url("trend_scraper",       settings.trend_scraper_port),
    "gap_detection":       _svc_health_url("gap_detection",       settings.gap_detection_port),
    "schema_factory":      _svc_health_url("schema_factory",      settings.schema_factory_port),
    "dashboard_backend":   _svc_health_url("dashboard_backend",   settings.dashboard_backend_port),
    "seo_engine":          _svc_health_url("seo_engine",          settings.seo_engine_port),
    "geo_engine":          _svc_health_url("geo_engine",          settings.geo_engine_port),
    "aeo_engine":          _svc_health_url("aeo_engine",          settings.aeo_engine_port),
}


@router.get("/health")
async def health():
    """Aggregated health check across all services."""
    from datetime import datetime, UTC

    service_checks = await _check_all_services()
    db_ok = await check_db_health()

    all_healthy = db_ok and all(
        v == "healthy" for v in service_checks.values()
    )

    return {
        "status":    "healthy" if all_healthy else "degraded",
        "version":   settings.app_version,
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": {
            "database": "healthy" if db_ok else "unhealthy",
            **service_checks,
        },
    }


@router.get("/health/db")
async def health_db():
    """Database-only health check."""
    ok = await check_db_health()
    return {"status": "healthy" if ok else "unhealthy", "service": "postgresql"}


async def _check_all_services() -> dict[str, str]:
    """Check all services concurrently with a 3-second timeout."""
    async def check(name: str, url: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(url)
                return name, "healthy" if r.status_code == 200 else "unhealthy"
        except Exception:
            return name, "unreachable"

    tasks = [check(name, url) for name, url in SERVICE_HEALTH_URLS.items()]
    results = await asyncio.gather(*tasks)
    return dict(results)
