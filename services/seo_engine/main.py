"""
DATA ENGINE — SEO Engine Service
Phase 4: Keyword analysis, metadata generation, structured data validation,
and on-page SEO scoring.

Swagger UI: http://localhost:8012/docs
ReDoc:      http://localhost:8012/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.seo_engine.keyword_analysis import KeywordAnalyzer
from services.seo_engine.validators import SEOValidator
from services.seo_engine.structured_data import StructuredDataChecker

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
        {"name": "seo", "description": "SEO analysis and scoring"},
        {"name": "health", "description": "Service health"},
    ],
)

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_analyzer = KeywordAnalyzer()
_validator = SEOValidator()
_checker = StructuredDataChecker()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/seo/analyze", response_model=SEOResult, tags=["seo"],
          summary="Run full SEO analysis on content")
async def analyze_seo(request: SEOAnalysisRequest = Body(...)):
    """Analyse content for SEO quality, keyword density, and structured data."""
    try:
        keyword_results = _analyzer.analyze(
            content=request.content,
            target_keywords=request.target_keywords,
        )
        issues, recommendations = _validator.validate(
            content=request.content,
            title=request.title,
            url=request.url,
            keywords=request.target_keywords,
        )
        return SEOResult(
            overall_score=keyword_results.get("score", 0.0),
            keyword_density=keyword_results.get("density", {}),
            matched_keywords=keyword_results.get("matched", []),
            issues=issues,
            recommendations=recommendations,
            readability_score=keyword_results.get("readability"),
            schema_valid=True,
        )
    except Exception as exc:
        logger.error("seo_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "seo_engine", "version": APP_VERSION}
