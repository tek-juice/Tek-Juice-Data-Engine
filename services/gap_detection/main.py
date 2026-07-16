"""
DATA ENGINE — Gap Detection Service
Phase 2/4: Detects content gaps between documents and trend signals.
Scores missing topics and generates prioritised recommendations.

Swagger UI: http://localhost:8008/docs
ReDoc:      http://localhost:8008/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.gap_detection.analyzer import GapAnalyzer
from services.gap_detection.scoring import GapScorer
from services.gap_detection.recommendation import RecommendationEngine

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

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_analyzer = GapAnalyzer()
_scorer = GapScorer()
_recommender = RecommendationEngine()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/gaps/analyze", response_model=GapResult, tags=["gaps"],
          summary="Analyze content gaps for a document")
async def analyze_gaps(request: GapAnalysisRequest = Body(...)):
    """
    Run a full gap analysis against a document.
    Compares document embeddings against recent trend signals.
    Returns missing topics, severity, and recommendations.
    """
    try:
        gaps = await _analyzer.analyze(
            document_id=request.document_id,
            tenant_id=request.tenant_id,
            gap_threshold=request.gap_threshold,
            max_gaps=request.max_gaps,
        )
        scored = _scorer.score(gaps)
        recommendations = _recommender.generate(gaps)
        return GapResult(
            gap_score=scored.score,
            severity=scored.severity,
            missing_topics=[g.get("title", "") for g in gaps],
            recommendations=recommendations,
            before_coverage=scored.before_coverage,
            after_coverage=None,
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


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "gap_detection", "version": APP_VERSION}
