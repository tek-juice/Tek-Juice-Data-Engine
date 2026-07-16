"""
DATA ENGINE — Celery AEO Tasks
Scheduled AEO (Answer Engine Optimisation) analysis tasks.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.run_aeo_analysis", bind=True, max_retries=3, default_retry_delay=30)
def run_aeo_analysis(self, document_id: str, tenant_id: str) -> dict:
    """Run AEO analysis for a single document."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from services.geo_engine.entity_mapper import EntityMapper
        from services.geo_engine.llm_visibility import LLMVisibilityScorer
        from services.geo_engine.citations import CitationReadinessAnalyser
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT raw_text FROM documents WHERE id = :id AND tenant_id = :tid"),
                {"id": document_id, "tid": tenant_id},
            )
            row = result.fetchone()

        if not row or not row.raw_text:
            return {"document_id": document_id, "skipped": True}

        content = row.raw_text
        mapper = EntityMapper()
        citations = CitationReadinessAnalyser()
        scorer = LLMVisibilityScorer()

        entities = await mapper.extract_entities(content)
        citation_result = citations.analyse(content)
        visibility = scorer.score(
            content=content,
            entity_count=len(entities),
            citation_score=citation_result.overall_score,
            context_richness=0.0,
        )

        logger.info(
            "aeo_analysis_complete",
            document_id=document_id,
            llm_visibility=visibility.overall_score,
            citation_score=citation_result.overall_score,
        )
        return {
            "document_id": document_id,
            "llm_visibility_score": visibility.overall_score,
            "citation_score": citation_result.overall_score,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("aeo_analysis_task_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.run_aeo_analysis_batch", bind=True)
def run_aeo_analysis_batch(self) -> dict:
    """Scheduled: run AEO analysis on all completed documents updated in the last 24h."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT id, tenant_id
                    FROM documents
                    WHERE status = 'completed'
                      AND updated_at >= NOW() - INTERVAL '24 hours'
                    LIMIT 50
                """)
            )
            docs = result.fetchall()

        for doc in docs:
            run_aeo_analysis.delay(str(doc.id), str(doc.tenant_id))

        logger.info("aeo_analysis_batch_dispatched", count=len(docs))
        return {"dispatched": len(docs)}

    return asyncio.run(_run())
