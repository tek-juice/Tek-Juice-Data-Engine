"""
DATA ENGINE — GEO Engine Service
Phase 4: Generative Engine Optimisation — entity extraction, knowledge graph
construction, LLM visibility scoring, and citation readiness analysis.

Swagger UI: http://localhost:8013/docs
ReDoc:      http://localhost:8013/redoc
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
from services.geo_engine.entity_mapper import EntityMapper
from services.geo_engine.llm_visibility import LLMVisibilityScorer
from services.geo_engine.citations import CitationAnalyzer

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class GEORequest(BaseModel):
    document_id: str
    tenant_id: str
    content: str = Field(..., min_length=50)
    target_models: list[str] = Field(
        default=["google_ai_overviews", "perplexity", "chatgpt"],
        description="Target AI engines to optimise for",
    )

    model_config = {"json_schema_extra": {"example": {
        "document_id": "uuid", "tenant_id": "uuid",
        "content": "OpenAI is an AI research company founded in 2015...",
        "target_models": ["google_ai_overviews", "perplexity", "chatgpt"],
    }}}


class GEOResult(BaseModel):
    llm_visibility_score: float
    entity_coverage: float
    citation_readiness: bool
    context_richness_score: float
    extracted_entities: list[dict[str, Any]]
    recommendations: list[str]
    optimised_content: str | None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("geo_engine_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("geo_engine_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — GEO Engine",
    description="""
## GEO Engine (Generative Engine Optimisation)

Optimises content to be cited by AI engines: Google AI Overviews, Perplexity, ChatGPT.

### How GEO Works
Traditional SEO targets keyword matching. GEO targets **entity relationships** and
**semantic trust/authority** — the signals that generative models use to decide
what to cite.

### Capabilities
- **Entity Extraction** — identifies People, Organisations, Products, Concepts
- **Knowledge Graph** — maps relationships between entities
- **LLM Visibility Score** — predicts how likely content is to be cited (0–100)
- **Citation Readiness** — checks First Sentence Rule, sameAs links, structured lists
- **Content Optimisation** — rewrites content to improve citation probability

### Target Engines
`Google AI Overviews` · `Perplexity` · `ChatGPT` · `Claude` · `Gemini`
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "geo", "description": "GEO analysis and optimisation"},
        {"name": "health", "description": "Service health"},
    ],
)

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_mapper = EntityMapper()
_scorer = LLMVisibilityScorer()
_citations = CitationAnalyzer()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/geo/analyze", response_model=GEOResult, tags=["geo"],
          summary="Run full GEO analysis on content")
async def analyze_geo(request: GEORequest = Body(...)):
    """
    Analyse content for GEO optimisation.
    Extracts entities, scores LLM visibility, and generates recommendations.
    """
    try:
        entities = await _mapper.extract_entities(request.content)
        visibility = _scorer.score(
            content=request.content,
            entities=entities,
            target_models=request.target_models,
        )
        citation_ready, recommendations = _citations.analyze(
            content=request.content,
            entities=entities,
        )
        return GEOResult(
            llm_visibility_score=visibility.score,
            entity_coverage=visibility.entity_coverage,
            citation_readiness=citation_ready,
            context_richness_score=visibility.context_richness,
            extracted_entities=[e.to_dict() if hasattr(e, "to_dict") else e for e in entities],
            recommendations=recommendations,
            optimised_content=None,
        )
    except Exception as exc:
        logger.error("geo_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "geo_engine", "version": APP_VERSION}
