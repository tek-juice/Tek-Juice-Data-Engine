"""
DATA ENGINE — Dashboard Analytics Router
System overview, gap comparison, document stats, and performance data.
Phase 3: Central Command — Before/After Gap Graph API.
"""

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from shared.authentication.jwt_handler import CurrentUser

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Dashboard Analytics"])


@router.get("/overview")
async def system_overview(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """High-level platform overview for the admin dashboard."""
    tenant_id = current_user.tenant_id

    results = await db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM documents WHERE tenant_id = :t AND status = 'completed') AS documents_completed,
                (SELECT COUNT(*) FROM documents WHERE tenant_id = :t AND status = 'failed')    AS documents_failed,
                (SELECT COUNT(*) FROM documents WHERE tenant_id = :t AND status = 'queued')    AS documents_queued,
                (SELECT COUNT(*) FROM chunks    WHERE tenant_id = :t)                          AS total_chunks,
                (SELECT COUNT(*) FROM embeddings WHERE tenant_id = :t)                         AS total_embeddings,
                (SELECT COUNT(*) FROM gap_analysis_results WHERE tenant_id = :t)               AS gap_analyses_run,
                (SELECT COUNT(*) FROM generated_schemas WHERE tenant_id = :t)                  AS schemas_generated,
                (SELECT COUNT(*) FROM scraped_trends WHERE scraped_at >= NOW() - INTERVAL '24 hours') AS trends_today
        """),
        {"t": tenant_id},
    )
    row = results.fetchone()
    return dict(row._mapping) if row else {}


@router.get("/gap-comparison/{document_id}")
async def gap_comparison(
    document_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Before/After gap comparison for a document.
    Returns coverage scores over time for timeline visualisation.
    """
    result = await db.execute(
        text("""
            SELECT
                gap_score,
                severity,
                before_coverage,
                after_coverage,
                missing_topics,
                recommendations,
                analysed_at
            FROM gap_analysis_results
            WHERE document_id = :doc_id AND tenant_id = :tenant_id
            ORDER BY analysed_at DESC
            LIMIT 10
        """),
        {"doc_id": document_id, "tenant_id": current_user.tenant_id},
    )
    rows = result.fetchall()
    analyses = [dict(row._mapping) for row in rows]

    # Build timeline for before/after chart
    timeline = [
        {
            "timestamp":       a["analysed_at"].isoformat() if hasattr(a["analysed_at"], "isoformat") else str(a["analysed_at"]),
            "gap_score":       float(a["gap_score"]),
            "before_coverage": float(a["before_coverage"] or 0),
            "after_coverage":  float(a["after_coverage"] or 0),
            "severity":        a["severity"],
        }
        for a in analyses
    ]

    latest = analyses[0] if analyses else {}
    return {
        "document_id":    document_id,
        "latest_gap":     latest.get("gap_score"),
        "latest_severity": latest.get("severity"),
        "before_coverage": latest.get("before_coverage"),
        "after_coverage":  latest.get("after_coverage"),
        "missing_topics":  latest.get("missing_topics", []),
        "recommendations": latest.get("recommendations", []),
        "timeline":        timeline,
    }


@router.get("/documents")
async def document_list(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
):
    """Paginated document list for admin management."""
    offset = (page - 1) * page_size
    where = "WHERE d.tenant_id = :t"
    params: dict = {"t": current_user.tenant_id, "limit": page_size, "offset": offset}
    if status:
        where += " AND d.status = :status"
        params["status"] = status

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM documents d {where}"), params
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text(f"""
            SELECT d.id, d.filename, d.source_type, d.status,
                   d.chunk_count, d.created_at, d.updated_at,
                   g.gap_score, g.severity
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT gap_score, severity FROM gap_analysis_results
                WHERE document_id = d.id ORDER BY analysed_at DESC LIMIT 1
            ) g ON true
            {where}
            ORDER BY d.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    items = [dict(row._mapping) for row in result.fetchall()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/activity-log")
async def activity_log(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
):
    """Recent system activity log for the telemetry stream viewer."""
    result = await db.execute(
        text("""
            SELECT event_type, service, status, duration_ms, payload, created_at
            FROM telemetry_events
            WHERE (tenant_id = :t OR tenant_id IS NULL)
              AND created_at >= NOW() - :hours * INTERVAL '1 hour'
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"t": current_user.tenant_id, "hours": hours, "limit": limit},
    )
    return [dict(row._mapping) for row in result.fetchall()]
