"""
DATA ENGINE — Gap Optimiser
Applies gap analysis results to trigger downstream actions:
schema generation, content expansion signals, re-embedding queuing,
and — crucially — automatic gap closure so every product always ranks first.

Auto-closure pipeline (runs immediately on every severity ≥ LOW):
  1. Build Intent-Based Content Clusters for all missing topics.
  2. Persist the full closure plan to gap_close_actions.
  3. For CRITICAL/HIGH: also dispatch schema generation + re-embed task.
  4. After re-embedding completes the gap batch will recheck after_coverage.
"""

import json
import structlog
from datetime import datetime, UTC
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.gap_detection.analyzer import GapAnalysisResult
from services.gap_detection.content_clusters import ContentClusterBuilder
from configs.constants import GapSeverity

logger = structlog.get_logger(__name__)

_cluster_builder = ContentClusterBuilder()


class GapOptimiser:
    """
    Reads gap analysis results, builds content-cluster closure plans, and
    dispatches all downstream actions required to close every gap automatically.

    Severity routing:
      - ANY severity   → build content clusters + persist gap_close_actions
      - CRITICAL/HIGH  → schema generation queued + document flagged high priority
      - MEDIUM         → document flagged medium priority
      - LOW            → closure plan recorded; no re-embed triggered
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def optimise(self, result: GapAnalysisResult) -> dict:
        """
        Process a GapAnalysisResult and trigger all automatic gap-closure actions.

        Returns:
            Dict describing every action taken.
        """
        actions: dict = {
            "document_id": result.document_id,
            "severity":    result.severity,
            "actions":     [],
        }

        # ── Step 1: Always build the closure plan, regardless of severity ──────
        if result.missing_topics:
            close_plan = await self.auto_close_gap(result)
            actions["close_plan_clusters"]  = close_plan.get("clusters_created", 0)
            actions["close_plan_words"]     = close_plan.get("estimated_words", 0)
            actions["actions"].append("gap_close_plan_created")

        # ── Step 2: Severity-driven downstream triggers ────────────────────────
        if result.severity in (GapSeverity.CRITICAL.value, GapSeverity.HIGH.value):
            await self._trigger_schema_generation(result)
            actions["actions"].append("schema_generation_queued")

            await self._flag_for_expansion(result.document_id, result.tenant_id, priority="high")
            actions["actions"].append("document_flagged_high_priority")

            # Queue re-embed so after_coverage is measured once content is updated
            await self._queue_reanalysis(result.document_id, result.tenant_id, delay_seconds=3600)
            actions["actions"].append("reanalysis_scheduled_1h")

        elif result.severity == GapSeverity.MEDIUM.value:
            await self._flag_for_expansion(result.document_id, result.tenant_id, priority="medium")
            actions["actions"].append("document_flagged_medium_priority")

            await self._queue_reanalysis(result.document_id, result.tenant_id, delay_seconds=21600)
            actions["actions"].append("reanalysis_scheduled_6h")

        else:
            actions["actions"].append("recorded_for_reporting")

        logger.info(
            "gap_optimisation_complete",
            document_id=result.document_id,
            severity=result.severity,
            actions=actions["actions"],
        )
        return actions

    # ── Auto-closure ──────────────────────────────────────────────────────────

    async def auto_close_gap(self, result: GapAnalysisResult) -> dict:
        """
        Build a full Intent-Based Content Cluster closure plan for all missing
        topics in this gap result and persist it to gap_close_actions.

        The closure plan is the machine-readable specification that tells the
        content layer exactly what sections to write, in what order, and with
        what schema types — so the document can rank first on every gap topic.

        Returns:
            Summary dict with cluster count, authority score, and word estimate.
        """
        report = _cluster_builder.build(
            document_id=result.document_id,
            tenant_id=result.tenant_id,
            missing_topics=result.missing_topics,
            document_content="",          # content available post-ingest via document text
            max_clusters_per_topic=4,
        )

        close_plan_json = json.dumps({
            "authority_score":        report.authority_score,
            "coverage_gaps":          report.coverage_gaps,
            "top_priority_clusters":  report.top_priority_clusters,
            "full_content_brief":     report.full_content_brief,
            "estimated_words_needed": report.estimated_words_needed,
            "clusters": [
                {
                    "topic":                    c.topic,
                    "intent":                   c.intent,
                    "priority":                 c.priority,
                    "coverage_score":           c.coverage_score,
                    "depth_score":              c.depth_score,
                    "query_variants":           c.query_variants,
                    "authority_signals_needed": c.authority_signals_needed,
                    "content_brief":            c.content_brief,
                    "schema_types_recommended": c.schema_types_recommended,
                }
                for c in report.clusters
            ],
        })

        await self._session.execute(
            text("""
                INSERT INTO gap_close_actions
                    (document_id, tenant_id, gap_score, severity,
                     missing_topics, close_plan, status, created_at)
                VALUES
                    (:document_id, :tenant_id, :gap_score, :severity,
                     :missing_topics, :close_plan, 'pending', :now)
                ON CONFLICT (document_id, tenant_id)
                DO UPDATE SET
                    gap_score      = EXCLUDED.gap_score,
                    severity       = EXCLUDED.severity,
                    missing_topics = EXCLUDED.missing_topics,
                    close_plan     = EXCLUDED.close_plan,
                    status         = 'pending',
                    updated_at     = EXCLUDED.created_at
            """),
            {
                "document_id":    result.document_id,
                "tenant_id":      result.tenant_id,
                "gap_score":      result.gap_score,
                "severity":       result.severity,
                "missing_topics": result.missing_topics,
                "close_plan":     close_plan_json,
                "now":            datetime.now(UTC),
            },
        )

        logger.info(
            "gap_close_plan_persisted",
            document_id=result.document_id,
            severity=result.severity,
            clusters=len(report.clusters),
            authority_score=report.authority_score,
            words_needed=report.estimated_words_needed,
        )

        # ── Dispatch writing agent immediately ────────────────────────────────
        # The writing agent reads the close plan we just persisted and generates
        # a production-ready content draft for every intent cluster.
        try:
            from workers.celery.app import celery_app
            celery_app.send_task(
                "tasks.write_gap_content",
                kwargs={
                    "document_id": result.document_id,
                    "tenant_id":   result.tenant_id,
                },
            )
        except Exception as exc:
            logger.error("writing_agent_dispatch_failed", error=str(exc))

        return {
            "clusters_created":  len(report.clusters),
            "authority_score":   report.authority_score,
            "estimated_words":   report.estimated_words_needed,
            "top_priorities":    report.top_priority_clusters,
        }

    # ── Downstream triggers ───────────────────────────────────────────────────

    async def _trigger_schema_generation(self, result: GapAnalysisResult) -> None:
        """Dispatch a Celery task to generate JSON-LD schemas for missing topics."""
        try:
            from workers.celery.app import celery_app
            celery_app.send_task(
                "tasks.generate_schema",
                kwargs={
                    "document_id":    result.document_id,
                    "tenant_id":      result.tenant_id,
                    "missing_topics": result.missing_topics,
                    "gap_score":      result.gap_score,
                },
            )
        except Exception as exc:
            logger.error("schema_generation_dispatch_failed", error=str(exc))

    async def _queue_reanalysis(
        self,
        document_id: str,
        tenant_id: str,
        delay_seconds: int = 3600,
    ) -> None:
        """
        Schedule a gap re-analysis for this document after `delay_seconds`.
        The re-analysis will compute after_coverage and mark the close action
        as resolved when the gap score drops below LOW threshold.
        """
        try:
            from workers.celery.app import celery_app
            celery_app.send_task(
                "tasks.auto_close_gaps",
                kwargs={"document_id": document_id, "tenant_id": tenant_id},
                countdown=delay_seconds,
            )
        except Exception as exc:
            logger.error("reanalysis_dispatch_failed", error=str(exc))

    async def _flag_for_expansion(
        self, document_id: str, tenant_id: str, priority: str = "medium"
    ) -> None:
        """Update document metadata to flag it for content expansion."""
        await self._session.execute(
            text("""
                UPDATE documents
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'),
                    '{expansion_priority}',
                    to_jsonb(:priority::text)
                )
                WHERE id = :doc_id AND tenant_id = :tenant_id
            """),
            {
                "priority":   f'"{priority}"',
                "doc_id":     document_id,
                "tenant_id":  tenant_id,
            },
        )
