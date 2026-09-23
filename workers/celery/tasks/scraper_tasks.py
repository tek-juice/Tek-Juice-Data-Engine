"""
DATA ENGINE — Celery Scraper Tasks
Scheduled trend scraping and embedding of scraped content.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.scrape_trends", bind=True, max_retries=2, default_retry_delay=60)
def scrape_trends(self, sources: list | None = None, queries: list | None = None) -> dict:
    """Scheduled: scrape trends from configured sources."""
    async def _run():
        from services.trend_scraper.scheduler import TrendScraperScheduler
        scheduler = TrendScraperScheduler()
        result = await scheduler.run(sources=sources, queries=queries)
        logger.info("trend_scraping_task_complete", **result)
        return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("trend_scraping_task_failed", error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.embed_scraped_trends", bind=True, max_retries=3, default_retry_delay=30)
def embed_scraped_trends(self) -> dict:
    """Embed scraped trend snippets that are missing embeddings."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from services.embedding_service.embedding_pipeline import EmbeddingPipeline
        from sqlalchemy import text

        _settings = get_settings()
        dims = _settings.embedding_dimension
        col = f"embedding_{dims}"

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(f"""
                    SELECT id, COALESCE(snippet, title, query) AS text
                    FROM scraped_trends
                    WHERE {col} IS NULL
                      AND COALESCE(snippet, title, query) IS NOT NULL
                    ORDER BY scraped_at DESC
                    LIMIT 100
                """)
            )
            rows = result.fetchall()

        if not rows:
            return {"embedded": 0}

        texts = [row.text for row in rows]
        pipeline = EmbeddingPipeline()
        embeddings, _, _ = await pipeline.embed(texts)

        async with AsyncSessionLocal() as session:
            for row, embedding in zip(rows, embeddings):
                # asyncpg rejects ::vector cast on bind params — inline the literal
                embedding_str = str(embedding).replace(" ", "")
                await session.execute(
                    text(f"UPDATE scraped_trends SET {col} = '{embedding_str}'::vector({dims}) WHERE id = :id"),
                    {"id": row.id},
                )
            await session.commit()

        logger.info("trend_embeddings_updated", count=len(rows))
        return {"embedded": len(rows)}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("trend_embedding_failed", error=str(exc))
        raise self.retry(exc=exc)
