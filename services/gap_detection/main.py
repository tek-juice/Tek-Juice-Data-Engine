"""
DATA ENGINE — Gap Detection Service
Phase 2/4: Detects content gaps between documents and trend signals.
Scores missing topics and generates prioritised recommendations.

Swagger UI: http://localhost:8008/docs
ReDoc:      http://localhost:8008/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body, Depends
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db, get_db_session
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.gap_detection.analyzer import GapAnalyzer, GapAnalysisResult
from services.gap_detection.optimizer import GapOptimiser
from services.gap_detection.recommendation import RecommendationEngine
from services.gap_detection.content_clusters import ContentClusterBuilder

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class GapAnalysisRequest(BaseModel):
    document_id: str
    tenant_id: str
    gap_threshold: float = Field(default=0.40, ge=0.0, le=1.0,
                                  description="Min distance to count as a gap (0=no gap, 1=total gap)")
    max_gaps: int = Field(default=20, ge=1, le=100)

    model_config = {"json_schema_extra": {"example": {
        "document_id": "uuid",
        "tenant_id": "uuid",
        "gap_threshold": 0.40,
        "max_gaps": 20,
    }}}


class GapResult(BaseModel):
    gap_score: float
    severity: str
    missing_topics: list[str]
    recommendations: list[str]
    before_coverage: float | None
    after_coverage: float | None


class ContentClustersRequest(BaseModel):
    document_id: str
    tenant_id: str
    missing_topics: list[str] = Field(..., min_length=1)
    document_content: str = ""
    max_clusters_per_topic: int = Field(default=4, ge=1, le=8)

    model_config = {"json_schema_extra": {"example": {
        "document_id": "uuid",
        "tenant_id": "uuid",
        "missing_topics": ["AI inventory management", "multi-channel sync"],
        "document_content": "Our platform helps businesses manage stock across channels...",
        "max_clusters_per_topic": 4,
    }}}


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("gap_detection_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("gap_detection_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Gap Detection",
    description="""
## Gap Detection Service

Identifies topics and trends that are NOT covered in a document's embeddings.

### How it works
1. Fetches recent scraped trends (last 7 days) from the Vector Vault
2. Computes cosine distance between each trend embedding and the document embeddings
3. Trends with distance > `gap_threshold` are flagged as missing content
4. Scores gaps by severity: `low → medium → high → critical`
5. Generates actionable recommendations for each gap

### Severity Thresholds
| Score | Severity |
|-------|----------|
| < 0.25 | low |
| 0.25–0.50 | medium |
| 0.50–0.75 | high |
| > 0.75 | critical |
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "gaps", "description": "Gap detection and analysis"},
        {"name": "health", "description": "Service health"},
    ],
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_recommender = RecommendationEngine()
_cluster_builder = ContentClusterBuilder()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/gaps/analyze", response_model=GapResult, tags=["gaps"],
          summary="Analyze content gaps for a document")
async def analyze_gaps(
    request: GapAnalysisRequest = Body(...),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Run a full gap analysis against a document.
    Compares document embeddings against recent trend signals.
    Returns missing topics, severity, and recommendations.
    """
    try:
        analyser = GapAnalyzer(session)
        result: GapAnalysisResult = await analyser.analyse(
            document_id=request.document_id,
            tenant_id=request.tenant_id,
        )
        recommendations = _recommender.generate(
            missing_topics=result.missing_topics,
            covered_topics=result.covered_topics,
            gap_score=result.gap_score,
            severity=result.severity,
        )
        return GapResult(
            gap_score=result.gap_score,
            severity=result.severity,
            missing_topics=result.missing_topics,
            recommendations=recommendations,
            before_coverage=result.before_coverage,
            after_coverage=result.after_coverage,
        )
    except Exception as exc:
        logger.error("gap_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/gaps/history/{{document_id}}", tags=["gaps"],
         summary="Get gap analysis history for a document")
async def gap_history(document_id: str, tenant_id: str):
    """Returns all previous gap analysis results for a document."""
    try:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT * FROM gap_analysis_results WHERE document_id=:doc AND tenant_id=:ten ORDER BY analysed_at DESC LIMIT 20"),
                {"doc": document_id, "ten": tenant_id},
            )
            rows = [dict(r._mapping) for r in result.fetchall()]
        return {"document_id": document_id, "history": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/gaps/clusters", tags=["gaps"],
          summary="Build intent-based content clusters from missing topics")
async def build_content_clusters(request: ContentClustersRequest = Body(...)):
    """
    Build Intent-Based Content Clusters (GEO Pillar 4) from gap analysis missing topics.

    For each missing topic, generates query variants across all intent types
    (definitional, procedural, causal, quantitative, comparative, authority,
    commercial, troubleshooting), scores existing coverage, and produces a
    prioritised content brief telling writers exactly what to add.

    Returns a ContentClusterReport with authority score and full content brief.
    """
    try:
        report = _cluster_builder.build(
            document_id=request.document_id,
            tenant_id=request.tenant_id,
            missing_topics=request.missing_topics,
            document_content=request.document_content,
            max_clusters_per_topic=request.max_clusters_per_topic,
        )
        return _cluster_builder.to_dict(report)
    except Exception as exc:
        logger.error("content_clusters_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/gaps/write/{{document_id}}", tags=["gaps"],
          summary="Trigger the LLM Writing Agent for a document immediately")
async def trigger_writing_agent(
    document_id: str,
    tenant_id: str,
):
    """
    Dispatch `tasks.write_gap_content` for a document right now.

    Use this after updating content to regenerate drafts on-demand, or to
    force the writing cycle outside the scheduled batch window.

    The task runs asynchronously — poll `GET /gaps/drafts/{document_id}` to
    see the results once the Celery worker completes.
    """
    try:
        from workers.celery.app import celery_app
        celery_app.send_task(
            "tasks.write_gap_content",
            kwargs={"document_id": document_id, "tenant_id": tenant_id},
        )
        return {
            "status":      "queued",
            "document_id": document_id,
            "message":     "Writing agent dispatched. Poll /gaps/drafts/{document_id} for results.",
        }
    except Exception as exc:
        logger.error("trigger_writing_agent_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/gaps/drafts/{{document_id}}", tags=["gaps"],
         summary="Get all LLM-generated content drafts for a document")
async def get_content_drafts(
    document_id: str,
    tenant_id: str,
    status: str | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Return all content drafts the LLM Writing Agent has generated for this
    document, optionally filtered by status (draft | embedded | approved | rejected).

    Each row contains the full draft_text, the target query_variants, the
    recommended schema_types, and authority_signals — everything needed to
    publish the content and update the source document.
    """
    try:
        from sqlalchemy import text
        query = """
            SELECT id::text, topic, intent, priority, draft_text, word_count,
                   query_variants, schema_types, authority_signals,
                   content_brief, model_used, provider_used,
                   status, generated_at::text
            FROM gap_content_drafts
            WHERE document_id = :doc_id AND tenant_id = :tenant_id
        """
        params: dict = {"doc_id": document_id, "tenant_id": tenant_id}
        if status:
            query  += " AND status = :status"
            params["status"] = status
        query += " ORDER BY priority ASC"

        result = await session.execute(text(query), params)
        rows   = [dict(r._mapping) for r in result.fetchall()]
        total_words = sum(r.get("word_count", 0) for r in rows)
        return {
            "document_id":   document_id,
            "drafts":        rows,
            "count":         len(rows),
            "total_words":   total_words,
        }
    except Exception as exc:
        logger.error("get_content_drafts_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/gaps/close/{{document_id}}", tags=["gaps"],
          summary="Immediately trigger auto gap-closure for a document")
async def trigger_auto_close(
    document_id: str,
    tenant_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Run the full gap-detection → closure-plan pipeline right now for a single
    document.  Useful to force immediate closure after content has been updated.

    Equivalent to dispatching the `tasks.auto_close_gaps` Celery task but
    executes synchronously so the caller sees the result immediately.
    """
    try:
        analyser  = GapAnalyzer(session)
        result: GapAnalysisResult = await analyser.analyse(
            document_id=document_id,
            tenant_id=tenant_id,
        )
        optimiser = GapOptimiser(session)
        actions   = await optimiser.optimise(result)
        await session.commit()
        return {
            "document_id":      document_id,
            "gap_score":        result.gap_score,
            "severity":         result.severity,
            "actions":          actions.get("actions", []),
            "clusters_created": actions.get("close_plan_clusters", 0),
            "estimated_words":  actions.get("close_plan_words", 0),
        }
    except Exception as exc:
        logger.error("trigger_auto_close_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/gaps/close-actions/{{document_id}}", tags=["gaps"],
         summary="Get the current auto-closure plan for a document")
async def get_close_action(
    document_id: str,
    tenant_id: str,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Return the live gap_close_actions record for a document — the full
    Intent-Based Content Cluster plan specifying exactly what to write to
    close every gap and rank first.
    """
    try:
        from sqlalchemy import text
        result = await session.execute(
            text("""
                SELECT gap_score, severity, missing_topics, close_plan,
                       status, created_at, updated_at, resolved_at
                FROM gap_close_actions
                WHERE document_id = :doc_id AND tenant_id = :tenant_id
                LIMIT 1
            """),
            {"doc_id": document_id, "tenant_id": tenant_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="No gap close action found for this document.",
            )
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_close_action_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "gap_detection", "version": APP_VERSION}
