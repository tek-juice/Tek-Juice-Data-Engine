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


@router.get("/tenant/me")
async def get_current_tenant(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Return the current tenant's profile and connection status."""
    try:
        result = await db.execute(
            text("""
                SELECT
                    t.id::text,
                    t.name,
                    t.slug,
                    t.tier,
                    t.is_active,
                    t.website_url,
                    t.platform_type,
                    t.injection_status,
                    t.last_crawled_at,
                    t.created_at,
                    (SELECT COUNT(*) FROM documents WHERE tenant_id = t.id AND status = 'completed') AS documents_count,
                    (SELECT COUNT(*) FROM gap_analysis_results WHERE tenant_id = t.id) AS gap_analyses_count
                FROM tenants t
                WHERE t.id = :tid
            """),
            {"tid": current_user.tenant_id},
        )
        row = result.fetchone()
        if not row:
            return {"id": current_user.tenant_id, "name": "Unknown"}
        data = dict(row._mapping)
        # Serialise datetimes
        for key in ("last_crawled_at", "created_at"):
            if data.get(key) and hasattr(data[key], "isoformat"):
                data[key] = data[key].isoformat()
        return data
    except Exception as exc:
        logger.error("get_current_tenant_failed", error=str(exc))
        return {"id": current_user.tenant_id, "error": str(exc)}


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


@router.get("/tenant/me")
async def my_tenant_performance(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Performance snapshot for the calling tenant (scoped by JWT/API key)."""
    tid = current_user.tenant_id

    # ── Tenant summary row ────────────────────────────────────────────────────
    tenant_row = await db.execute(
        text("""
            SELECT
                t.id                                                          AS tenant_id,
                t.name,
                t.tier                                                        AS plan,
                t.created_at,
                COALESCE(
                    (SELECT MAX(te.created_at) FROM telemetry_events te WHERE te.tenant_id = t.id),
                    t.created_at
                )                                                             AS last_active,
                (SELECT COUNT(*) FROM documents d WHERE d.tenant_id = t.id)  AS documents_total,
                (SELECT COUNT(*) FROM gap_close_actions g WHERE g.tenant_id = t.id AND g.status = 'completed') AS gaps_closed,
                (SELECT COUNT(*) FROM gap_content_drafts gcd WHERE gcd.tenant_id = t.id) AS drafts_generated,
                COALESCE(
                    (SELECT AVG(gap_score) FROM gap_analysis_results WHERE tenant_id = t.id), 0
                )                                                             AS avg_gap_score,
                0                                                             AS avg_seo_score,
                0                                                             AS avg_geo_score,
                0                                                             AS avg_aeo_score,
                0                                                             AS coverage_pct,
                0                                                             AS api_calls_total,
                0                                                             AS api_calls_24h,
                0                                                             AS webhooks_delivered,
                0.0                                                           AS error_rate
            FROM tenants t
            WHERE t.id = :tid
        """),
        {"tid": tid},
    )
    tenant = tenant_row.fetchone()
    if not tenant:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Tenant not found")
    t = dict(tenant._mapping)

    # ── Gap history — last 14 days ────────────────────────────────────────────
    gap_hist_result = await db.execute(
        text("""
            SELECT
                DATE(analysed_at)              AS day,
                AVG(gap_score)                 AS gap_score,
                AVG(COALESCE(before_coverage, 0)) AS coverage_before,
                AVG(COALESCE(after_coverage, 0))  AS coverage_after,
                0                              AS gaps_closed
            FROM gap_analysis_results
            WHERE tenant_id = :tid
              AND analysed_at >= NOW() - INTERVAL '14 days'
            GROUP BY DATE(analysed_at)
            ORDER BY day DESC
            LIMIT 14
        """),
        {"tid": tid},
    )
    gap_history = [
        {
            "day":             str(r["day"]),
            "gap_score":       float(r["gap_score"] or 0),
            "coverage_before": float(r["coverage_before"] or 0),
            "coverage_after":  float(r["coverage_after"] or 0),
            "gaps_closed":     int(r["gaps_closed"] or 0),
        }
        for r in [dict(row._mapping) for row in gap_hist_result.fetchall()]
    ]

    # ── Visibility history — last 24 h (hourly telemetry) ────────────────────
    vis_result = await db.execute(
        text("""
            SELECT
                DATE_TRUNC('hour', created_at) AS time,
                0                              AS seo,
                0                              AS geo,
                0                              AS aeo
            FROM telemetry_events
            WHERE tenant_id = :tid
              AND created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY DATE_TRUNC('hour', created_at)
            ORDER BY time DESC
            LIMIT 24
        """),
        {"tid": tid},
    )
    visibility_history = [
        {
            "time": row["time"].isoformat() if hasattr(row["time"], "isoformat") else str(row["time"]),
            "seo":  int(row["seo"] or 0),
            "geo":  int(row["geo"] or 0),
            "aeo":  int(row["aeo"] or 0),
        }
        for row in [dict(r._mapping) for r in vis_result.fetchall()]
    ]

    # ── Recent activity — last 50 events ─────────────────────────────────────
    act_result = await db.execute(
        text("""
            SELECT event_type, service, status, duration_ms, payload, created_at
            FROM telemetry_events
            WHERE tenant_id = :tid
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"tid": tid},
    )
    recent_activity = [
        {
            "event_type":  r["event_type"],
            "service":     r["service"],
            "status":      r["status"],
            "duration_ms": r["duration_ms"],
            "payload":     r["payload"],
            "timestamp":   r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
        }
        for r in [dict(row._mapping) for row in act_result.fetchall()]
    ]

    return {
        "tenant": {
            "tenant_id":          str(t["tenant_id"]),
            "name":               t["name"] or "My Product",
            "key_prefix":         "de_prod",
            "plan":               t["plan"] or "free",
            "created_at":         t["created_at"].isoformat() if hasattr(t["created_at"], "isoformat") else str(t["created_at"]),
            "last_active":        t["last_active"].isoformat() if t["last_active"] and hasattr(t["last_active"], "isoformat") else None,
            "documents_total":    int(t["documents_total"] or 0),
            "api_calls_total":    int(t["api_calls_total"] or 0),
            "api_calls_24h":      int(t["api_calls_24h"] or 0),
            "webhooks_delivered": int(t["webhooks_delivered"] or 0),
            "avg_gap_score":      float(t["avg_gap_score"] or 0),
            "avg_seo_score":      float(t["avg_seo_score"] or 0),
            "avg_geo_score":      float(t["avg_geo_score"] or 0),
            "avg_aeo_score":      float(t["avg_aeo_score"] or 0),
            "coverage_pct":       float(t["coverage_pct"] or 0),
            "drafts_generated":   int(t["drafts_generated"] or 0),
            "gaps_closed":        int(t["gaps_closed"] or 0),
            "error_rate":         float(t["error_rate"] or 0),
            "status":             "active",
        },
        "usage_timeseries":   [],
        "recent_activity":    recent_activity,
        "gap_history":        gap_history,
        "visibility_history": visibility_history,
    }
