"""
DATA ENGINE — Vector Vault Service
Phase 1/4: Stores, indexes, and retrieves vector embeddings via pgvector.
Supports cosine similarity search with namespace isolation per embedding model.

Swagger UI: http://localhost:8004/docs
ReDoc:      http://localhost:8004/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import Depends, FastAPI, HTTPException, Body, Query
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
from services.vector_vault.search import VectorSearch
from services.vector_vault.pgvector import PGVectorStore

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class StoreRequest(BaseModel):
    chunk_id: str
    document_id: str
    tenant_id: str
    embedding: list[float] = Field(..., description="Vector to store")
    provider: str
    model: str
    metadata: dict = Field(default_factory=dict)

    model_config = {"json_schema_extra": {"example": {
        "chunk_id": "uuid", "document_id": "uuid", "tenant_id": "uuid",
        "embedding": [0.1, 0.2, 0.3],
        "provider": "gemini", "model": "models/text-embedding-004",
    }}}


class SearchRequest(BaseModel):
    query_vector: list[float] = Field(..., description="Query embedding vector")
    tenant_id: str
    top_k: int = Field(default=10, ge=1, le=100)
    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    document_id: str | None = None

    model_config = {"json_schema_extra": {"example": {
        "query_vector": [0.1, 0.2, 0.3],
        "tenant_id": "uuid",
        "top_k": 10,
        "similarity_threshold": 0.75,
    }}}


class SearchResult(BaseModel):
    chunk_id: str
    text: str
    similarity: float
    provider: str
    model: str
    metadata: dict


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("vector_vault_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("vector_vault_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Vector Vault",
    description="""
## Vector Vault

Stores and retrieves vector embeddings using PostgreSQL + pgvector.

### Namespace Isolation
Embeddings are stored in separate columns by dimension to prevent cross-model
distance calculations. `embedding_768` for Gemini/Jina, `embedding_1536` for OpenAI.

### Search
Uses HNSW approximate nearest-neighbour index for sub-millisecond similarity search.

### Typical Flow
`Embedding → **Vector Vault** → Gap Detection / Semantic Engine`
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "vectors", "description": "Vector storage and retrieval"},
        {"name": "health", "description": "Service health"},
    ],
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)


# ── Routes ────────────────────────────────────────────────────────────────────
# PGVectorStore and VectorSearch require an AsyncSession — inject per request.

@app.post(f"{API_PREFIX}/vectors/store", tags=["vectors"],
          summary="Store an embedding vector")
async def store_vector(
    request: StoreRequest = Body(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Store a single embedding vector into the correct namespace column."""
    try:
        store = PGVectorStore(db)
        result = await store.store_vectors([request])
        return {"status": "stored", "count": result}
    except Exception as exc:
        logger.error("vector_store_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/vectors/search", response_model=list[SearchResult],
          tags=["vectors"], summary="Semantic similarity search")
async def search_vectors(
    request: SearchRequest = Body(...),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Find the top-k most similar chunks to the query vector.
    Only searches within the same embedding model namespace.
    """
    try:
        search = VectorSearch(db)
        results = await search.search(
            query_embedding=request.query_vector,
            tenant_id=request.tenant_id,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
            document_id=request.document_id,
        )
        return results
    except Exception as exc:
        logger.error("vector_search_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete(f"{API_PREFIX}/vectors/document/{{document_id}}", tags=["vectors"],
            summary="Delete all vectors for a document")
async def delete_document_vectors(
    document_id: str,
    tenant_id: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete all stored embeddings for a specific document."""
    try:
        store = PGVectorStore(db)
        deleted = await store.delete_by_document(document_id=document_id, tenant_id=tenant_id)
        return {"deleted": deleted, "document_id": document_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "vector_vault", "version": APP_VERSION}
