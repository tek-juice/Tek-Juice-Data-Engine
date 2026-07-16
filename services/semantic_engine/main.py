"""
DATA ENGINE — Semantic Engine Service
Phase 4: Semantic comparison, clustering, re-ranking, and similarity scoring.

Swagger UI: http://localhost:8007/docs
ReDoc:      http://localhost:8007/redoc
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
from services.semantic_engine.comparator import SemanticComparator
from services.semantic_engine.cosine_similarity import cosine_similarity
from services.semantic_engine.ranking import SemanticRanker

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    vector_a: list[float] = Field(..., description="First embedding vector")
    vector_b: list[float] = Field(..., description="Second embedding vector")


class RankRequest(BaseModel):
    query_vector: list[float]
    candidates: list[dict] = Field(..., description="List of {id, vector, text} objects")
    top_k: int = Field(default=10, ge=1, le=100)


class ClusterRequest(BaseModel):
    vectors: list[list[float]]
    n_clusters: int = Field(default=5, ge=2, le=50)
    labels: list[str] | None = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("semantic_engine_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("semantic_engine_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Semantic Engine",
    description="""
## Semantic Engine

Provides semantic comparison, ranking, clustering and similarity scoring
between document embeddings.

### Capabilities
- **Cosine similarity** between any two vectors
- **Re-ranking** — sort candidate chunks by relevance to a query
- **Clustering** — group similar content chunks together
- **Document comparison** — compare two documents by average embedding
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "semantic", "description": "Semantic operations"},
        {"name": "health", "description": "Service health"},
    ],
)

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_comparator = SemanticComparator()
_ranker = SemanticRanker()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/semantic/compare", tags=["semantic"],
          summary="Compare two vectors with cosine similarity")
async def compare(request: CompareRequest = Body(...)):
    """Returns the cosine similarity score between two embedding vectors (0–1)."""
    try:
        score = cosine_similarity(request.vector_a, request.vector_b)
        return {"similarity": round(score, 6), "dimensions": len(request.vector_a)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/semantic/rank", tags=["semantic"],
          summary="Re-rank candidates by semantic similarity to query")
async def rank(request: RankRequest = Body(...)):
    """Sort a list of candidate chunks by their similarity to the query vector."""
    try:
        ranked = await _ranker.rank(
            query_vector=request.query_vector,
            candidates=request.candidates,
            top_k=request.top_k,
        )
        return {"ranked": ranked, "count": len(ranked)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "semantic_engine", "version": APP_VERSION}
