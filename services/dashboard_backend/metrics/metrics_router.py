"""
DATA ENGINE — Dashboard Metrics Router
Aggregated performance and throughput metrics for dashboard charts.
Phase 3: Central Command monitoring interface.
"""

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from shared.authentication.jwt_handler import CurrentUser
from services.telemetry_service.collectors.event_collector import EventCollector

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Dashboard Metrics"])


@router.get("/metrics/summary")
async def metrics_summary(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    hours: int = Query(24, ge=1, le=720),
):
    """Event counts, error rates, and avg durations grouped by service."""
    collector = EventCollector(db)
    return await collector.get_event_summary(
        hours=hours,
        tenant_id=current_user.tenant_id,
    )


@router.get("/metrics/throughput")
async def throughput_timeseries(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    hours: int = Query(24, ge=1, le=168),
    interval_minutes: int = Query(30, ge=5, le=60),
):
    """Events-per-interval time series for throughput charts."""
    collector = EventCollector(db)
    return await collector.get_throughput_timeseries(
        hours=hours,
        interval_minutes=interval_minutes,
    )


@router.get("/metrics/errors")
async def error_log(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
):
    """Most recent error events across all services."""
    collector = EventCollector(db)
    return await collector.get_error_log(limit=limit)


@router.get("/metrics/pipeline")
async def pipeline_metrics(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Per-stage pipeline throughput and latency for the last 24 hours."""
    from sqlalchemy import text
    result = await db.execute(
        text("""
            SELECT
                event_type,
                COUNT(*) AS count,
                AVG(duration_ms) AS avg_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                MAX(duration_ms) AS max_ms,
                SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END) AS errors
            FROM telemetry_events
            WHERE (tenant_id = :t OR tenant_id IS NULL)
              AND created_at >= NOW() - INTERVAL '24 hours'
              AND event_type LIKE 'document.%'
            GROUP BY event_type
            ORDER BY count DESC
        """),
        {"t": current_user.tenant_id},
    )
    return [dict(row._mapping) for row in result.fetchall()]
