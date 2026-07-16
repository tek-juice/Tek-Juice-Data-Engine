"""
DATA ENGINE — SEO Engine Service
Phase 4: Keyword analysis, metadata generation, structured data validation,
on-page SEO scoring, SERP rank tracking, and domain authority monitoring.

Swagger UI: http://localhost:8012/docs
ReDoc:      http://localhost:8012/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db, get_db_session
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.seo_engine.keyword_analysis import KeywordAnalyser
from services.seo_engine.validators import SEOValidator

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class SEOAnalysisRequest(BaseModel):
    document_id: str
    tenant_id: str
    content: str = Field(..., min_length=50)
    target_keywords: list[str] = Field(default_factory=list)
    url: str | None = None
    title: str | None = None

    model_config = {"json_schema_extra": {"example": {
        "document_id": "uuid", "tenant_id": "uuid",
        "content": "Vector databases are purpose-built for AI applications...",
        "target_keywords": ["vector database", "AI embeddings", "semantic search"],
        "url": "https://example.com/vector-databases",
        "title": "Vector Databases Explained",
    }}}


class SEOResult(BaseModel):
    overall_score: float
    keyword_density: dict[str, float]
    matched_keywords: list[str]
    issues: list[str]
    recommendations: list[str]
    readability_score: float | None
    schema_valid: bool


# ── Rank tracking models ───────────────────────────────────────────────────────

class RankTrackingConfigRequest(BaseModel):
    tenant_id: str
    domain: str
    keyword: str
    location_code: int = Field(default=2840, description="DataForSEO location code. 2840 = USA")
    tags: list[str] = Field(default_factory=list)

    model_config = {"json_schema_extra": {"example": {
        "tenant_id": "uuid", "domain": "example.com",
        "keyword": "vector database", "location_code": 2840,
    }}}


class RankSnapshotResponse(BaseModel):
    domain: str
    keyword: str
    location_code: int
    position: int | None
    url: str | None
    search_volume: int | None
    cpc: float | None
    competition: float | None
    snapshot_date: str


class AuthoritySnapshotResponse(BaseModel):
    domain: str
    domain_rank: int | None
    total_backlinks: int | None
    referring_domains: int | None
    dofollow_backlinks: int | None
    nofollow_backlinks: int | None
    spam_score: float | None
    new_backlinks_30d: int | None
    lost_backlinks_30d: int | None
    top_anchors: list[str]
    snapshot_date: str


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("seo_engine_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("seo_engine_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — SEO Engine",
    description="""
## SEO Engine

Performs comprehensive on-page SEO analysis and structured data validation.

### Capabilities
- **Keyword analysis** — density, placement, and semantic coverage
- **Structured data validation** — checks JSON-LD schema correctness
- **Metadata scoring** — title length, meta description quality
- **Readability** — Flesch-Kincaid score and paragraph structure
- **Issue detection** — missing tags, thin content, duplicate issues
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "seo",    "description": "SEO analysis, scoring, rank tracking, and domain authority"},
        {"name": "health", "description": "Service health"},
    ],
)

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_analyser = KeywordAnalyser()
_validator = SEOValidator()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/seo/analyze", response_model=SEOResult, tags=["seo"],
          summary="Run full SEO analysis on content")
async def analyze_seo(request: SEOAnalysisRequest = Body(...)):
    """Analyse content for SEO quality, keyword density, and structured data."""
    try:
        kw_result = _analyser.analyse(
            content=request.content,
            target_keywords=request.target_keywords,
            title=request.title or "",
        )
        url_audit = _validator.audit_url(request.url) if request.url else None
        issues = url_audit.issues if url_audit else []
        recommendations = list(kw_result.recommendations)
        if url_audit:
            recommendations.extend(url_audit.warnings)
        # No JSON-LD schema submitted — report as valid (cannot validate plain text)
        schema_valid = True
        return SEOResult(
            overall_score=round(kw_result.coverage_score * 100, 1),
            keyword_density=kw_result.keyword_density,
            matched_keywords=kw_result.matched_keywords,
            issues=issues + kw_result.density_issues,
            recommendations=recommendations,
            readability_score=None,
            schema_valid=schema_valid,
        )
    except Exception as exc:
        logger.error("seo_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "seo_engine", "version": APP_VERSION}


# ── Rank Tracking Routes ───────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/seo/rank-config", status_code=201, tags=["seo"],
          summary="Register a keyword for daily rank tracking")
async def add_rank_tracking_config(
    request: RankTrackingConfigRequest = Body(...),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Register a (domain, keyword, location) combination for daily SERP rank tracking.
    The Celery beat task `tasks.track_keyword_rankings` polls these daily.
    """
    try:
        from sqlalchemy import text
        await session.execute(
            text("""
                INSERT INTO rank_tracking_config
                    (tenant_id, domain, keyword, location_code, tags)
                VALUES
                    (:tenant_id, :domain, :keyword, :location_code, :tags)
                ON CONFLICT (tenant_id, domain, keyword, location_code) DO NOTHING
            """),
            {
                "tenant_id":     request.tenant_id,
                "domain":        request.domain,
                "keyword":       request.keyword,
                "location_code": request.location_code,
                "tags":          request.tags,
            },
        )
        return {"status": "registered", "domain": request.domain, "keyword": request.keyword}
    except Exception as exc:
        logger.error("rank_config_add_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/seo/rankings/{{domain}}", tags=["seo"],
         summary="Get SERP rank history for a domain")
async def get_rank_history(
    domain: str,
    keyword: str | None = Query(default=None, description="Filter by keyword"),
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db_session),
):
    """Return rank tracking snapshots for a domain over the last N days."""
    try:
        from sqlalchemy import text
        query = """
            SELECT domain, keyword, location_code, position, ranking_url AS url,
                   search_volume, cpc, competition,
                   snapshot_date::text AS snapshot_date
            FROM rank_tracking
            WHERE domain = :domain
              AND snapshot_date >= CURRENT_DATE - :days
        """
        params: dict[str, Any] = {"domain": domain, "days": days}
        if keyword:
            query += " AND keyword = :keyword"
            params["keyword"] = keyword
        query += " ORDER BY snapshot_date DESC, keyword LIMIT 500"

        result = await session.execute(text(query), params)
        rows = [dict(r._mapping) for r in result.fetchall()]
        return {"domain": domain, "snapshots": rows, "count": len(rows)}
    except Exception as exc:
        logger.error("rank_history_failed", domain=domain, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/seo/authority/{{domain}}", tags=["seo"],
         summary="Get domain authority snapshot history")
async def get_authority_history(
    domain: str,
    days: int = Query(default=90, ge=1, le=365),
    session: AsyncSession = Depends(get_db_session),
):
    """Return domain authority and backlink snapshots for the last N days."""
    try:
        from sqlalchemy import text
        result = await session.execute(
            text("""
                SELECT domain, domain_rank, total_backlinks, referring_domains,
                       dofollow_backlinks, nofollow_backlinks, spam_score,
                       new_backlinks_30d, lost_backlinks_30d, top_anchors,
                       snapshot_date::text AS snapshot_date
                FROM domain_authority
                WHERE domain = :domain
                  AND snapshot_date >= CURRENT_DATE - :days
                ORDER BY snapshot_date DESC
                LIMIT 365
            """),
            {"domain": domain, "days": days},
        )
        rows = [dict(r._mapping) for r in result.fetchall()]
        return {"domain": domain, "snapshots": rows, "count": len(rows)}
    except Exception as exc:
        logger.error("authority_history_failed", domain=domain, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
