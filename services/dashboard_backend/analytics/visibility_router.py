"""
DATA ENGINE — Product Visibility Dashboard Router
The read-only dashboard every connected product owner watches to see results.

Shows: published content, injection status, ranking signals, crawl history,
content gap closure progress, and quality score trends.

All endpoints are read-only. Product owners log in with their API key
(generated during onboarding) or their email/password JWT.
"""

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from shared.authentication.jwt_handler import CurrentUser

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Product Visibility Dashboard"])


# ── GET /dashboard/visibility/overview ───────────────────────────────────────

@router.get("/visibility/overview")
async def visibility_overview(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    The main dashboard overview for a connected product owner.
    Shows everything the engine has done and is currently doing.
    """
    tid = current_user.tenant_id

    # Tenant + connection status
    tenant_row = await db.execute(
        text("""
            SELECT name, website_url, platform_type, injection_status,
                   onboarding_completed_at, last_crawled_at, crawl_config
            FROM tenants
            WHERE id = :tid
        """),
        {"tid": tid},
    )
    tenant = tenant_row.fetchone()

    # Published content stats
    published_row = await db.execute(
        text("""
            SELECT
                COUNT(*)                                              AS total_published,
                COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '7 days')
                                                                      AS published_this_week,
                COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '24 hours')
                                                                      AS published_today,
                AVG(quality_score)  FILTER (WHERE quality_score IS NOT NULL)
                                                                      AS avg_quality_score,
                AVG(geo_score)      FILTER (WHERE geo_score IS NOT NULL)
                                                                      AS avg_geo_score,
                AVG(aeo_score)      FILTER (WHERE aeo_score IS NOT NULL)
                                                                      AS avg_aeo_score,
                COUNT(*) FILTER (WHERE beats_paid_ads = true)         AS content_beating_ads
            FROM gap_content_drafts
            WHERE tenant_id = :tid
              AND status    = 'published'
        """),
        {"tid": tid},
    )
    pub = published_row.fetchone()

    # Pending drafts (written but not yet injected)
    pending_row = await db.execute(
        text("""
            SELECT COUNT(*) AS pending_injection
            FROM gap_content_drafts
            WHERE tenant_id = :tid AND status = 'embedded'
        """),
        {"tid": tid},
    )
    pending = pending_row.fetchone()

    # Gap analysis summary
    gap_row = await db.execute(
        text("""
            SELECT
                COUNT(DISTINCT document_id)                           AS documents_analysed,
                AVG(gap_score)                                        AS avg_gap_score,
                AVG(before_coverage)                                  AS avg_before_coverage,
                AVG(after_coverage) FILTER (WHERE after_coverage IS NOT NULL)
                                                                      AS avg_after_coverage
            FROM gap_analysis_results
            WHERE tenant_id = :tid
        """),
        {"tid": tid},
    )
    gap = gap_row.fetchone()

    # Pages crawled total
    crawl_row = await db.execute(
        text("""
            SELECT COUNT(*) AS pages_crawled
            FROM documents
            WHERE tenant_id = :tid AND source_type = 'html' AND status = 'completed'
        """),
        {"tid": tid},
    )
    crawl = crawl_row.fetchone()

    # Most recently published content
    recent_row = await db.execute(
        text("""
            SELECT topic, intent, quality_score, geo_score, aeo_score,
                   composite_score, beats_paid_ads, projected_position, published_at
            FROM gap_content_drafts
            WHERE tenant_id = :tid AND status = 'published'
            ORDER BY published_at DESC NULLS LAST
            LIMIT 5
        """),
        {"tid": tid},
    )
    recent_published = [dict(r._mapping) for r in recent_row.fetchall()]

    return {
        "product": {
            "name":                 tenant.name           if tenant else None,
            "website_url":          tenant.website_url    if tenant else None,
            "platform":             tenant.platform_type  if tenant else None,
            "connection_status":    tenant.injection_status if tenant else "unknown",
            "connected_at":         tenant.onboarding_completed_at.isoformat()
                                    if tenant and tenant.onboarding_completed_at else None,
            "last_crawled_at":      tenant.last_crawled_at.isoformat()
                                    if tenant and tenant.last_crawled_at else None,
        },
        "content": {
            "total_published":      int(pub.total_published)      if pub else 0,
            "published_this_week":  int(pub.published_this_week)  if pub else 0,
            "published_today":      int(pub.published_today)      if pub else 0,
            "pending_injection":    int(pending.pending_injection) if pending else 0,
            "avg_quality_score":    round(float(pub.avg_quality_score), 2)
                                    if pub and pub.avg_quality_score else None,
            "avg_geo_score":        round(float(pub.avg_geo_score), 2)
                                    if pub and pub.avg_geo_score else None,
            "avg_aeo_score":        round(float(pub.avg_aeo_score), 2)
                                    if pub and pub.avg_aeo_score else None,
            "content_beating_ads":  int(pub.content_beating_ads) if pub else 0,
        },
        "gaps": {
            "documents_analysed":   int(gap.documents_analysed)   if gap else 0,
            "avg_gap_score":        round(float(gap.avg_gap_score), 3)
                                    if gap and gap.avg_gap_score else None,
            "avg_coverage_before":  round(float(gap.avg_before_coverage), 3)
                                    if gap and gap.avg_before_coverage else None,
            "avg_coverage_after":   round(float(gap.avg_after_coverage), 3)
                                    if gap and gap.avg_after_coverage else None,
        },
        "crawl": {
            "pages_crawled": int(crawl.pages_crawled) if crawl else 0,
        },
        "recent_published": recent_published,
    }


# ── GET /dashboard/visibility/published ──────────────────────────────────────

@router.get("/visibility/published")
async def published_content(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    page: int      = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    Full paginated list of all content published to the connected product.
    Ordered by most recently published first.
    """
    tid    = current_user.tenant_id
    offset = (page - 1) * page_size

    count_row = await db.execute(
        text("SELECT COUNT(*) FROM gap_content_drafts WHERE tenant_id=:tid AND status='published'"),
        {"tid": tid},
    )
    total = count_row.scalar() or 0

    result = await db.execute(
        text("""
            SELECT topic, intent, word_count, quality_score, geo_score,
                   aeo_score, composite_score, beats_paid_ads,
                   projected_position, published_at, model_used
            FROM gap_content_drafts
            WHERE tenant_id = :tid AND status = 'published'
            ORDER BY published_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """),
        {"tid": tid, "limit": page_size, "offset": offset},
    )
    items = [dict(r._mapping) for r in result.fetchall()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── GET /dashboard/visibility/connection ─────────────────────────────────────

@router.get("/visibility/connection")
async def connection_status(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Live connection status — is the injection bridge working,
    when was the last crawl, what platform is connected.
    Used by the wizard's final confirmation screen and the dashboard header.
    """
    tid = current_user.tenant_id

    result = await db.execute(
        text("""
            SELECT t.name, t.website_url, t.platform_type,
                   t.injection_status, t.injection_config,
                   t.onboarding_completed_at, t.last_crawled_at,
                   t.crawl_config,
                   ak.key_prefix,
                   ak.last_used_at AS api_key_last_used
            FROM tenants t
            LEFT JOIN api_keys ak ON ak.tenant_id = t.id AND ak.is_active = TRUE
            WHERE t.id = :tid
            LIMIT 1
        """),
        {"tid": tid},
    )
    row = result.fetchone()
    if not row:
        from shared.exceptions.base import DocumentNotFoundError
        raise DocumentNotFoundError()

    return {
        "product_name":          row.name,
        "website_url":           row.website_url,
        "platform":              row.platform_type,
        "injection_status":      row.injection_status,
        "onboarding_completed":  row.onboarding_completed_at.isoformat()
                                 if row.onboarding_completed_at else None,
        "last_crawled_at":       row.last_crawled_at.isoformat()
                                 if row.last_crawled_at else None,
        "crawl_config":          row.crawl_config,
        "api_key_prefix":        row.key_prefix,
        "api_key_last_used":     row.api_key_last_used.isoformat()
                                 if row.api_key_last_used else None,
        "engine_url":            "http://54.86.109.228:9600",
    }


# ── GET /dashboard/visibility/gaps ───────────────────────────────────────────

@router.get("/visibility/gaps")
async def gap_progress(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Content gap closure progress per document.
    Shows before/after coverage and which gaps are still open.
    """
    tid = current_user.tenant_id

    result = await db.execute(
        text("""
            SELECT
                d.filename,
                g.gap_score,
                g.severity,
                g.before_coverage,
                g.after_coverage,
                g.missing_topics,
                g.recommendations,
                g.analysed_at,
                c.status AS close_status,
                c.resolved_at
            FROM documents d
            JOIN LATERAL (
                SELECT gap_score, severity, before_coverage, after_coverage,
                       missing_topics, recommendations, analysed_at
                FROM gap_analysis_results
                WHERE document_id = d.id
                ORDER BY analysed_at DESC LIMIT 1
            ) g ON true
            LEFT JOIN gap_close_actions c ON c.document_id = d.id
            WHERE d.tenant_id = :tid AND d.status = 'completed'
            ORDER BY g.gap_score DESC
            LIMIT :limit
        """),
        {"tid": tid, "limit": limit},
    )
    return [dict(r._mapping) for r in result.fetchall()]


# ── GET /dashboard/visibility/quality-scores ─────────────────────────────────

@router.get("/visibility/quality-scores")
async def quality_score_breakdown(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Distribution of quality scores for all published content.
    Shows how much content is ranking in each Google Ad Rank band.
    """
    tid = current_user.tenant_id

    result = await db.execute(
        text("""
            SELECT
                CASE
                    WHEN quality_score >= 9.5 THEN 'AI Overview (#0)'
                    WHEN quality_score >= 8.5 THEN 'Position 1 + Featured Snippet'
                    WHEN quality_score >= 8.0 THEN 'Position 1–2, above most paid ads'
                    WHEN quality_score >= 7.0 THEN 'Position 2–3, competitive'
                    WHEN quality_score >= 5.0 THEN 'Position 4–6'
                    ELSE 'Page 2 or lower'
                END                             AS rank_band,
                COUNT(*)                        AS content_count,
                AVG(quality_score)              AS avg_score,
                COUNT(*) FILTER (WHERE beats_paid_ads = true) AS beats_ads
            FROM gap_content_drafts
            WHERE tenant_id   = :tid
              AND status      = 'published'
              AND quality_score IS NOT NULL
            GROUP BY 1
            ORDER BY MIN(quality_score) DESC
        """),
        {"tid": tid},
    )
    return [dict(r._mapping) for r in result.fetchall()]
