"""
DATA ENGINE — Celery Auto-Gap-Closure Tasks

These tasks form the automatic gap-closure loop that ensures every product
using the Engine always closes its content gaps and ranks first.

Pipeline per document:
  1. Run gap analysis (GapAnalyzer)               → detect missing topics
  2. Build content-cluster closure plan            → what exactly to write
  3. Persist close plan to gap_close_actions       → content layer reads this
  4. Dispatch schema generation (CRITICAL/HIGH)    → structured data upgraded
  5. Schedule re-analysis after content update     → verify after_coverage

The batch task sweeps ALL active tenant documents every N hours so no product
is ever left with an un-closed gap.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.auto_close_gaps",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def auto_close_gaps(self, document_id: str, tenant_id: str) -> dict:
    """
    Run the full gap-detection → closure-plan pipeline for a single document.

    Steps:
      1. Analyse the document against recent trends.
      2. Build the Intent-Based Content Cluster closure plan.
      3. Persist the plan to gap_close_actions.
      4. Dispatch severity-driven schema / re-analysis tasks.
      5. Update after_coverage on gap_analysis_results once gaps close.

    This task is also called as the scheduled re-analysis to verify that
    previously opened gaps have been resolved after content is updated.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from services.gap_detection.analyzer import GapAnalyzer
        from services.gap_detection.optimizer import GapOptimiser
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            analyser  = GapAnalyzer(session)
            result    = await analyser.analyse(
                document_id=document_id,
                tenant_id=tenant_id,
            )

            optimiser = GapOptimiser(session)
            actions   = await optimiser.optimise(result)

            # ── Update after_coverage on the latest gap_analysis_results row ──
            # after_coverage = current before_coverage (i.e. post-update score)
            after_coverage = result.before_coverage
            # PostgreSQL doesn't support ORDER BY/LIMIT in UPDATE —
            # use a subquery to target only the most recent row
            await session.execute(
                text("""
                    UPDATE gap_analysis_results
                    SET after_coverage = :after_cov
                    WHERE id = (
                        SELECT id FROM gap_analysis_results
                        WHERE document_id = :doc_id
                          AND tenant_id   = :tenant_id
                          AND after_coverage IS NULL
                        ORDER BY analysed_at DESC
                        LIMIT 1
                    )
                """),
                {
                    "after_cov": after_coverage,
                    "doc_id":    document_id,
                    "tenant_id": tenant_id,
                },
            )

            # ── Mark close action resolved if gap_score is now negligible ─────
            gap_resolved = result.gap_score < 0.10
            if gap_resolved:
                await session.execute(
                    text("""
                        UPDATE gap_close_actions
                        SET status     = 'resolved',
                            resolved_at = NOW()
                        WHERE document_id = :doc_id
                          AND tenant_id   = :tenant_id
                          AND status      = 'pending'
                    """),
                    {"doc_id": document_id, "tenant_id": tenant_id},
                )
                logger.info(
                    "gap_closed_verified",
                    document_id=document_id,
                    gap_score=result.gap_score,
                    after_coverage=after_coverage,
                )

            await session.commit()

        # ── Fire webhooks ─────────────────────────────────────────────────────
        from workers.celery.tasks.webhook_tasks import deliver_webhook
        from services.webhooks.webhook_events import (
            build_gap_detected,
            build_gap_resolved,
        )

        gap_payload = build_gap_detected(
            document_id=document_id,
            tenant_id=tenant_id,
            gap_score=result.gap_score,
            severity=result.severity,
            missing_topics=result.missing_topics,
            before_coverage=result.before_coverage,
            top_priority_clusters=actions.get("top_priorities"),
            estimated_words_needed=actions.get("close_plan_words", 0),
        )
        deliver_webhook.delay(tenant_id, "gap.detected", gap_payload)

        if gap_resolved:
            resolved_payload = build_gap_resolved(
                document_id=document_id,
                tenant_id=tenant_id,
                gap_score=result.gap_score,
                after_coverage=after_coverage,
                before_coverage=result.before_coverage,
            )
            deliver_webhook.delay(tenant_id, "gap.resolved", resolved_payload)

        logger.info(
            "auto_close_gaps_complete",
            document_id=document_id,
            gap_score=result.gap_score,
            severity=result.severity,
            actions=actions.get("actions", []),
        )
        return {
            "document_id":      document_id,
            "gap_score":        result.gap_score,
            "severity":         result.severity,
            "after_coverage":   after_coverage,
            "actions":          actions.get("actions", []),
            "clusters_created": actions.get("close_plan_clusters", 0),
            "estimated_words":  actions.get("close_plan_words", 0),
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("auto_close_gaps_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.auto_close_gaps_batch", bind=True)
def auto_close_gaps_batch(self) -> dict:
    """
    Scheduled sweep: dispatch auto_close_gaps for every active document that
    either has never been analysed, has an open gap_close_action, or whose
    last analysis is older than the configured interval.

    This guarantees that every product is continuously pushed toward first-place
    rankings — not just documents that were recently updated.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from configs.settings import get_settings
        from sqlalchemy import text

        _settings = get_settings()
        interval  = _settings.gap_auto_close_interval_seconds

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(f"""
                    SELECT d.id, d.tenant_id
                    FROM documents d
                    LEFT JOIN gap_analysis_results g
                           ON g.document_id = d.id
                          AND g.analysed_at = (
                                SELECT MAX(analysed_at) FROM gap_analysis_results
                                WHERE document_id = d.id
                              )
                    LEFT JOIN gap_close_actions c
                           ON c.document_id = d.id
                          AND c.tenant_id   = d.tenant_id
                    WHERE d.status = 'completed'
                      AND (
                            g.id IS NULL                                          -- never analysed
                            OR g.analysed_at < NOW() - INTERVAL '{interval} seconds'  -- stale
                            OR c.status = 'pending'                               -- open gap
                          )
                    ORDER BY COALESCE(g.gap_score, 1.0) DESC                      -- worst gaps first
                    LIMIT 100
                """)
            )
            docs = result.fetchall()

        for doc in docs:
            auto_close_gaps.delay(str(doc.id), str(doc.tenant_id))

        logger.info("auto_close_gaps_batch_dispatched", count=len(docs))
        return {"dispatched": len(docs)}

    return asyncio.run(_run())
