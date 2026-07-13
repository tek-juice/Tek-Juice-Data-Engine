"""
DATA ENGINE — Celery Gap Detection & Schema Tasks
Runs gap analysis on documents and triggers schema generation.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(name="tasks.run_gap_analysis", bind=True, max_retries=3, default_retry_delay=30)
def run_gap_analysis(self, document_id: str, tenant_id: str) -> dict:
    """Run gap analysis for a single document."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from services.gap_detection.analyzer import GapAnalyzer
        from services.gap_detection.optimizer import GapOptimiser

        async with AsyncSessionLocal() as session:
            analyser = GapAnalyzer(session)
            result = await analyser.analyse(document_id=document_id, tenant_id=tenant_id)

            optimiser = GapOptimiser(session)
            actions = await optimiser.optimise(result)
            await session.commit()

        logger.info(
            "gap_analysis_complete",
            document_id=document_id,
            gap_score=result.gap_score,
            severity=result.severity,
        )
        return {
            "document_id": document_id,
            "gap_score": result.gap_score,
            "severity": result.severity,
            "actions": actions.get("actions", []),
        }

    try:
        return asyncio.get_event_loop().run_until_complete(_run())
    except Exception as exc:
        logger.error("gap_analysis_task_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.run_gap_analysis_batch", bind=True)
def run_gap_analysis_batch(self) -> dict:
    """Scheduled: run gap analysis on all completed documents updated in the last 24h."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT d.id, d.tenant_id
                    FROM documents d
                    LEFT JOIN gap_analysis_results g ON g.document_id = d.id
                    WHERE d.status = 'completed'
                      AND d.updated_at >= NOW() - INTERVAL '24 hours'
                      AND (g.id IS NULL OR g.analysed_at < NOW() - INTERVAL '6 hours')
                    LIMIT 50
                """)
            )
            docs = result.fetchall()

        for doc in docs:
            run_gap_analysis.delay(str(doc.id), str(doc.tenant_id))

        logger.info("gap_analysis_batch_dispatched", count=len(docs))
        return {"dispatched": len(docs)}

    return asyncio.get_event_loop().run_until_complete(_run())


@shared_task(name="tasks.generate_schema", bind=True, max_retries=3, default_retry_delay=30)
def generate_schema(
    self,
    document_id: str,
    tenant_id: str,
    missing_topics: list,
    gap_score: float,
) -> dict:
    """Generate JSON-LD schemas for a document with identified gaps."""
    async def _run():
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.schema_factory.llm_factory import LLMSchemaFactory

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT raw_text FROM documents WHERE id = :id"),
                {"id": document_id},
            )
            row = result.fetchone()
            content = (row.raw_text or "")[:500] if row else ""

        factory = LLMSchemaFactory()
        schema_result = await factory.generate_from_gap(
            document_id=document_id,
            tenant_id=tenant_id,
            missing_topics=missing_topics,
            content_excerpt=content,
        )
        logger.info("schema_generation_complete", document_id=document_id)
        return {"document_id": document_id, "schemas_generated": 1}

    try:
        return asyncio.get_event_loop().run_until_complete(_run())
    except Exception as exc:
        logger.error("schema_generation_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)
