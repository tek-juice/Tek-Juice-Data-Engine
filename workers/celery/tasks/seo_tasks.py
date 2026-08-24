"""
DATA ENGINE — Celery SEO Intelligence Tasks
Scheduled rank tracking and domain authority monitoring.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.track_keyword_rankings", bind=True, max_retries=2, default_retry_delay=60)
def track_keyword_rankings(self) -> dict:
    """
    Scheduled: fetch current SERP rankings for all tracked keywords.
    Reads tracked keywords from the rank_tracking_config table and
    dispatches DataForSEO API calls for each domain/keyword/location.
    """
    async def _run():
        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from services.seo_engine.rank_tracker import RankTracker
        from sqlalchemy import text

        _settings = get_settings()
        tracker = RankTracker(
            api_login=_settings.dataforseo_login,
            api_password=_settings.dataforseo_password,
        )

        # Load all active tracked keywords
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT domain, keyword, location_code
                    FROM rank_tracking_config
                    WHERE is_active = TRUE
                    LIMIT 500
                """)
            )
            items = [
                {"domain": r.domain, "keyword": r.keyword, "location_code": r.location_code}
                for r in result.fetchall()
            ]

        if not items:
            logger.info("rank_tracking_no_items_configured")
            return {"tracked": 0}

        result_data = await tracker.track_batch(items)
        persisted   = await tracker.persist(result_data.snapshots)

        logger.info(
            "rank_tracking_complete",
            tracked=result_data.tracked,
            not_ranked=result_data.not_ranked,
            persisted=persisted,
            errors=result_data.errors,
        )
        return {
            "tracked":    result_data.tracked,
            "not_ranked": result_data.not_ranked,
            "persisted":  persisted,
            "errors":     result_data.errors,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("rank_tracking_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.track_domain_authority", bind=True, max_retries=2, default_retry_delay=60)
def track_domain_authority(self) -> dict:
    """
    Scheduled (daily): fetch domain authority and backlink metrics for all
    tracked domains and persist daily snapshots.
    """
    async def _run():
        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from services.seo_engine.authority import AuthorityTracker
        from sqlalchemy import text

        _settings = get_settings()
        tracker = AuthorityTracker(
            api_login=_settings.dataforseo_login,
            api_password=_settings.dataforseo_password,
        )

        # Load all unique domains from rank_tracking_config
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT DISTINCT domain FROM rank_tracking_config WHERE is_active = TRUE")
            )
            domains = [r.domain for r in result.fetchall()]

        if not domains:
            logger.info("authority_tracking_no_domains_configured")
            return {"fetched": 0}

        result_data = await tracker.fetch_batch(domains)
        persisted   = await tracker.persist(result_data.snapshots)

        logger.info(
            "authority_tracking_complete",
            fetched=result_data.fetched,
            persisted=persisted,
            errors=result_data.errors,
        )
        return {
            "fetched":   result_data.fetched,
            "persisted": persisted,
            "errors":    result_data.errors,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("authority_tracking_task_failed", error=str(exc))
        raise self.retry(exc=exc)
