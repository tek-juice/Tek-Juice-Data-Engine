"""
DATA ENGINE — Gap Optimiser
Applies gap analysis results to trigger downstream actions:
schema generation, content expansion signals, re-embedding queuing.
"""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.gap_detection.analyzer import GapAnalysisResult
from configs.constants import GapSeverity

logger = structlog.get_logger(__name__)


class GapOptimiser:
    """
    Reads gap analysis results and dispatches optimisation actions.
    Actions are severity-driven:
      - CRITICAL/HIGH → trigger LLM schema generation + Celery task
      - MEDIUM        → flag document for content expansion
      - LOW           → record for dashboard reporting only
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def optimise(self, result: GapAnalysisResult) -> dict:
        """
        Process a GapAnalysisResult and trigger appropriate optimisation actions.

        Returns:
            Dict describing actions taken.
        """
        actions: dict = {
            "document_id": result.document_id,
            "severity":    result.severity,
            "actions":     [],
        }

        if result.severity in (GapSeverity.CRITICAL.value, GapSeverity.HIGH.value):
            await self._trigger_schema_generation(result)
            actions["actions"].append("schema_generation_queued")

            await self._flag_for_expansion(result.document_id, result.tenant_id, priority="high")
            actions["actions"].append("document_flagged_high_priority")

        elif result.severity == GapSeverity.MEDIUM.value:
            await self._flag_for_expansion(result.document_id, result.tenant_id, priority="medium")
            actions["actions"].append("document_flagged_medium_priority")

        else:
            actions["actions"].append("recorded_for_reporting")

        logger.info(
            "gap_optimisation_complete",
            document_id=result.document_id,
            severity=result.severity,
            actions=actions["actions"],
        )
        return actions

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
                    :priority::jsonb
                )
                WHERE id = :doc_id AND tenant_id = :tenant_id
            """),
            {
                "priority":   f'"{priority}"',
                "doc_id":     document_id,
                "tenant_id":  tenant_id,
            },
        )
