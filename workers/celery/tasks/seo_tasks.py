"""
DATA ENGINE — Celery SEO Intelligence Tasks

Scheduled SearXNG rank tracking and domain search-visibility monitoring.
"""

import asyncio

import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.track_keyword_rankings",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def track_keyword_rankings(self) -> dict:
    """
    Scheduled: fetch current SERP rankings for all tracked keywords.

    Reads active tracked queries from rank_tracking_config and dispatches
    SearXNG searches for each tenant/domain/keyword combination.
    """

    async def _run():
        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from services.seo_engine.rank_tracker import RankTracker
        from sqlalchemy import text

        settings = get_settings()

        tracker = RankTracker(
            searxng_url=settings.searxng_url,
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT tenant_id, domain, keyword, language
                    FROM rank_tracking_config
                    WHERE is_active = TRUE
                    LIMIT 500
                    """
                )
            )

            items = [
                {
                    "tenant_id": r.tenant_id,
                    "domain": r.domain,
                    "keyword": r.keyword,
                    "language": r.language,
                }
                for r in result.fetchall()
            ]

        if not items:
            logger.info("rank_tracking_no_items_configured")
            return {"tracked": 0}

        result_data = await tracker.track_batch(items)

        # Each RankSnapshot carries its originating tenant_id.
        persisted = await tracker.persist(result_data.snapshots)

        logger.info(
            "rank_tracking_complete",
            tracked=result_data.tracked,
            not_ranked=result_data.not_ranked,
            persisted=persisted,
            errors=result_data.errors,
        )

        return {
            "tracked": result_data.tracked,
            "not_ranked": result_data.not_ranked,
            "persisted": persisted,
            "errors": result_data.errors,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("rank_tracking_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.track_domain_visibility",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def track_domain_visibility(self) -> dict:
    """
    Scheduled (daily): measure search visibility for all tracked domains.

    Each domain is evaluated against its active tracked queries using
    SearXNG. The resulting metrics are stored in domain_visibility.

    This measures search visibility, not backlink authority.
    """

    async def _run():
        from collections import defaultdict

        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from services.seo_engine.authority import AuthorityTracker
        from sqlalchemy import text

        settings = get_settings()

        tracker = AuthorityTracker(
            searxng_url=settings.searxng_url,
        )

        # Build tenant -> domain -> tracked queries.
        tenant_domains: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    """
                    SELECT tenant_id, domain, keyword
                    FROM rank_tracking_config
                    WHERE is_active = TRUE
                    ORDER BY tenant_id, domain, keyword
                    LIMIT 5000
                    """
                )
            )

            rows = result.fetchall()

        for row in rows:
            tenant_id = str(row.tenant_id)
            tenant_domains[tenant_id][row.domain].append(row.keyword)

        if not tenant_domains:
            logger.info("domain_visibility_no_queries_configured")
            return {
                "domains": 0,
                "queries": 0,
                "persisted": 0,
            }

        total_domains = 0
        total_queries = 0
        total_persisted = 0
        total_errors = 0

        for tenant_id, domain_queries in tenant_domains.items():
            total_domains += len(domain_queries)
            total_queries += sum(
                len(queries)
                for queries in domain_queries.values()
            )

            # AuthorityTracker now expects tenant-aware domain/query groups.
            result_data = await tracker.fetch_batch({
                tenant_id: domain_queries,
            })

            persisted = await tracker.persist(result_data.snapshots)

            total_persisted += persisted
            total_errors += result_data.errors

            logger.info(
                "domain_visibility_tenant_complete",
                tenant_id=tenant_id,
                domains=len(domain_queries),
                queries=sum(
                    len(queries)
                    for queries in domain_queries.values()
                ),
                fetched=result_data.fetched,
                persisted=persisted,
                errors=result_data.errors,
            )

        logger.info(
            "domain_visibility_complete",
            domains=total_domains,
            queries=total_queries,
            persisted=total_persisted,
            errors=total_errors,
        )

        return {
            "domains": total_domains,
            "queries": total_queries,
            "persisted": total_persisted,
            "errors": total_errors,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("domain_visibility_task_failed", error=str(exc))
        raise self.retry(exc=exc)
