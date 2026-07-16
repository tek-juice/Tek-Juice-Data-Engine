"""
DATA ENGINE — Health Router
Aggregates health checks across all registered services.
Phase 3: Central Command monitoring interface.
"""

import asyncio
import structlog
import httpx
from fastapi import APIRouter
from configs.database import check_db_health
from configs.settings import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Health"])
settings = get_settings()

SERVICE_HEALTH_URLS = {
    "ingestion_service":   f"http://localhost:{settings.ingestion_service_port}/health",
    "embedding_service":   f"http://localhost:{settings.embedding_service_port}/health",
    "vector_vault":        f"http://localhost:{settings.vector_vault_port}/health",
    "telemetry_service":   f"http://localhost:{settings.telemetry_service_port}/health",
    "trend_scraper":       f"http://localhost:{settings.trend_scraper_port}/health",
    "gap_detection":       f"http://localhost:{settings.gap_detection_port}/health",
    "schema_factory":      f"http://localhost:{settings.schema_factory_port}/health",
    "dashboard_backend":   f"http://localhost:{settings.dashboard_backend_port}/health",
    "seo_engine":          f"http://localhost:{settings.seo_engine_port}/health",
    "geo_engine":          f"http://localhost:{settings.geo_engine_port}/health",
    "aeo_engine":          f"http://localhost:{settings.aeo_engine_port}/health",
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
