"""
DATA ENGINE — Telemetry Event Collector
Collects and aggregates system events from all services.
"""

import structlog
from datetime import datetime, UTC, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class EventCollector:
    """
    Queries the telemetry_events table for aggregated metrics
    and time-series data for the dashboard backend.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_event_summary(
        self,
        hours: int = 24,
        tenant_id: str | None = None,
    ) -> dict:
        """Return event counts grouped by type and status for the last N hours."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        params: dict = {"since": since}
        where = "WHERE created_at >= :since"
        if tenant_id:
            where += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        result = await self._session.execute(
            text(f"""
                SELECT
                    event_type,
                    service,
                    status,
                    COUNT(*) AS count,
                    AVG(duration_ms) AS avg_duration_ms,
                    MAX(duration_ms) AS max_duration_ms
                FROM telemetry_events
                {where}
                GROUP BY event_type, service, status
                ORDER BY count DESC
            """),
            params,
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_throughput_timeseries(
        self,
        hours: int = 24,
        interval_minutes: int = 30,
    ) -> list[dict]:
        """Return events per interval bucket for throughput charting."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        result = await self._session.execute(
            text(f"""
                SELECT
                    date_trunc('hour', created_at)
                        + INTERVAL '{interval_minutes} minutes'
                        * FLOOR(EXTRACT(MINUTE FROM created_at) / {interval_minutes}) AS bucket,
                    COUNT(*) AS events,
                    SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END) AS failures
                FROM telemetry_events
                WHERE created_at >= :since
                GROUP BY bucket
                ORDER BY bucket
            """),
            {"since": since},
        )
        return [
            {
                "bucket": row.bucket.isoformat(),
                "events": row.events,
                "failures": row.failures,
            }
            for row in result.fetchall()
        ]

    async def get_error_log(self, limit: int = 50) -> list[dict]:
        """Return the most recent error events."""
        result = await self._session.execute(
            text("""
                SELECT event_type, service, payload, created_at
                FROM telemetry_events
                WHERE status = 'failure'
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return [dict(row._mapping) for row in result.fetchall()]
