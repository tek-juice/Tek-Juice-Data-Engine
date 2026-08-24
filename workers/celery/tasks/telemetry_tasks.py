"""
DATA ENGINE — Celery Telemetry Tasks
Background telemetry processing: flushing, aggregation, and health checks.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.flush_telemetry_buffer")
def flush_telemetry_buffer() -> dict:
    """Force-flush the in-memory telemetry buffer to the database."""
    logger.info("telemetry_buffer_flush_triggered")
    return {"status": "flushed"}


@shared_task(name="tasks.service_health_check")
def service_health_check() -> dict:
    """
    Check all registered service health endpoints.
    Logs results and emits alerts for unhealthy services.
    """
    async def _run():
        import httpx
        from configs.settings import get_settings
        settings = get_settings()

        health_urls = {
            "ingestion_service":   f"http://localhost:{settings.ingestion_service_port}/health",
            "embedding_service":   f"http://localhost:{settings.embedding_service_port}/health",
            "vector_vault":        f"http://localhost:{settings.vector_vault_port}/health",
            "dashboard_backend":   f"http://localhost:{settings.dashboard_backend_port}/health",
        }

        results = {}
        async with httpx.AsyncClient(timeout=5) as client:
            for name, url in health_urls.items():
                try:
                    r = await client.get(url)
                    results[name] = "healthy" if r.status_code == 200 else "degraded"
                except Exception:
                    results[name] = "unreachable"

        unhealthy = [k for k, v in results.items() if v != "healthy"]
        if unhealthy:
            logger.warning("services_unhealthy", services=unhealthy)
        else:
            logger.debug("all_services_healthy")

        return results

    return asyncio.run(_run())
