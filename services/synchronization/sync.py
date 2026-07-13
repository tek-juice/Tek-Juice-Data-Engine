"""
DATA ENGINE — Data Pool Synchronization
Phase 2: Merges all processed datasets into a unified, consistent data pool.
Ensures embeddings, schemas, gap results, and trends are all up to date.
"""

import json
import structlog
from datetime import datetime, UTC
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import AsyncSessionLocal
from shared.exceptions.base import DatabaseError

logger = structlog.get_logger(__name__)


@dataclass
class SyncReport:
    """Summary of a synchronization run."""
    sync_type: str
    records_synced: int = 0
    records_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class DataPoolSynchronizer:
    """
    Synchronizes all data pools:
    - Marks stale embeddings for re-processing
    - Consolidates gap analysis results
    - Purges expired trend data
    - Updates document processing counters
    - Triggers re-embedding for updated documents
    """

    async def run_full_sync(self) -> SyncReport:
        """Run all synchronization tasks in sequence."""
        report = SyncReport(sync_type="full_sync")
        start = datetime.now(UTC)

        async with AsyncSessionLocal() as session:
            await self._log_sync_start(session, "full_sync")

            try:
                n = await self._sync_document_chunk_counts(session)
                report.records_synced += n

                n = await self._purge_expired_trends(session)
                report.records_synced += n

                n = await self._consolidate_gap_results(session)
                report.records_synced += n

                n = await self._flag_stale_documents(session)
                report.records_synced += n

                await session.commit()
                report.completed_at = datetime.now(UTC).isoformat()
                report.duration_seconds = (datetime.now(UTC) - start).total_seconds()

                await self._log_sync_complete(session, "full_sync", report.records_synced)
                logger.info("data_pool_sync_complete", **report.__dict__)

            except Exception as exc:
                report.errors.append(str(exc))
                logger.error("data_pool_sync_failed", error=str(exc))
                await self._log_sync_failed(session, "full_sync", str(exc))

        return report

    async def _sync_document_chunk_counts(self, session: AsyncSession) -> int:
        """Update chunk_count on documents to match actual chunk records."""
        result = await session.execute(
            text("""
                UPDATE documents d
                SET chunk_count = sub.cnt
                FROM (
                    SELECT document_id, COUNT(*) AS cnt
                    FROM chunks
                    GROUP BY document_id
                ) sub
                WHERE d.id = sub.document_id
                  AND d.chunk_count != sub.cnt
                RETURNING d.id
            """)
        )
        count = result.rowcount or 0
        logger.debug("sync_chunk_counts_updated", count=count)
        return count

    async def _purge_expired_trends(self, session: AsyncSession) -> int:
        """Remove trend records older than 30 days to keep the pool fresh."""
        result = await session.execute(
            text("""
                DELETE FROM scraped_trends
                WHERE scraped_at < NOW() - INTERVAL '30 days'
                RETURNING id
            """)
        )
        count = result.rowcount or 0
        logger.debug("sync_trends_purged", count=count)
        return count

    async def _consolidate_gap_results(self, session: AsyncSession) -> int:
        """Keep only the most recent gap analysis per document."""
        result = await session.execute(
            text("""
                DELETE FROM gap_analysis_results g1
                USING gap_analysis_results g2
                WHERE g1.document_id = g2.document_id
                  AND g1.analysed_at < g2.analysed_at
                RETURNING g1.id
            """)
        )
        count = result.rowcount or 0
        logger.debug("sync_gap_results_consolidated", count=count)
        return count

    async def _flag_stale_documents(self, session: AsyncSession) -> int:
        """Flag documents with embeddings older than 7 days for re-processing."""
        result = await session.execute(
            text("""
                UPDATE documents
                SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'),
                    '{needs_reembedding}',
                    'true'::jsonb
                )
                WHERE status = 'completed'
                  AND updated_at < NOW() - INTERVAL '7 days'
                  AND (metadata->>'needs_reembedding') IS DISTINCT FROM 'true'
                RETURNING id
            """)
        )
        count = result.rowcount or 0
        logger.debug("sync_stale_documents_flagged", count=count)
        return count

    async def _log_sync_start(self, session: AsyncSession, sync_type: str) -> None:
        await session.execute(
            text("""
                INSERT INTO sync_log (sync_type, status) VALUES (:sync_type, 'started')
            """),
            {"sync_type": sync_type},
        )

    async def _log_sync_complete(self, session: AsyncSession, sync_type: str, count: int) -> None:
        await session.execute(
            text("""
                UPDATE sync_log SET status = 'completed', records_synced = :count,
                completed_at = NOW()
                WHERE sync_type = :sync_type AND status = 'started'
                  AND id = (
                      SELECT id FROM sync_log WHERE sync_type = :sync_type
                      ORDER BY started_at DESC LIMIT 1
                  )
            """),
            {"sync_type": sync_type, "count": count},
        )

    async def _log_sync_failed(self, session: AsyncSession, sync_type: str, error: str) -> None:
        await session.execute(
            text("""
                UPDATE sync_log SET status = 'failed', error_message = :error,
                completed_at = NOW()
                WHERE sync_type = :sync_type AND status = 'started'
            """),
            {"sync_type": sync_type, "error": error[:500]},
        )
