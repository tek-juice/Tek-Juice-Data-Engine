"""
DATA ENGINE — Celery Sync Tasks
Scheduled data pool synchronization and cache invalidation.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.sync_data_pool", bind=True, max_retries=2, default_retry_delay=60)
def sync_data_pool(self) -> dict:
    """Scheduled: run full data pool synchronization."""
    async def _run():
        from services.synchronization.sync import DataPoolSynchronizer
        syncer = DataPoolSynchronizer()
        report = await syncer.run_full_sync()
        logger.info(
            "data_pool_sync_task_complete",
            synced=report.records_synced,
            success=report.success,
        )
        return {
            "synced": report.records_synced,
            "success": report.success,
            "errors": report.errors,
            "duration_seconds": report.duration_seconds,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("sync_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.invalidate_cache_for_tenant")
def invalidate_cache_for_tenant(tenant_id: str) -> dict:
    """Invalidate all cached data for a specific tenant."""
    async def _run():
        from services.synchronization.cache import get_cache
        cache = await get_cache()
        deleted = await cache.delete_pattern(f"*:{tenant_id}:*")
        deleted += await cache.delete_pattern(f"search:{tenant_id}:*")
        logger.info("tenant_cache_invalidated", tenant_id=tenant_id, deleted=deleted)
        return {"tenant_id": tenant_id, "keys_deleted": deleted}

    return asyncio.run(_run())
