"""
DATA ENGINE — Embedding Service
Phase 1: Converts text chunks into vector embeddings using configured provider.
Supports Gemini, OpenAI, Voyage, and Jina with automatic fallback.

Swagger UI: http://localhost:8003/docs
ReDoc:      http://localhost:8003/redoc
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
from services.embedding_service.embedding_pipeline import EmbeddingPipeline

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, description="List of texts to embed")
    provider: str | None = Field(None, description="Override embedding provider (openai/gemini/voyage/jina)")
    batch_size: int | None = Field(None, ge=1, le=500, description="Batch size for API calls")

    model_config = {"json_schema_extra": {"example": {
        "texts": ["Vector databases store high-dimensional embeddings.", "Semantic search finds relevant content."],
        "provider": None,
    }}}


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    provider: str
    model: str
    dimensions: int
    count: int


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("embedding_service_starting", version=APP_VERSION,
                provider=settings.default_embedding_provider)
    await init_db()
    yield
    await dispose_db()
    logger.info("embedding_service_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Embedding Service",
    description="""
## Embedding Service

Converts text into high-dimensional vector embeddings for semantic search and similarity.

### Configured Provider
Current default: **{provider}** (`{model}`, {dims}d)

### Fallback Chain
If the primary provider fails, the engine automatically tries the next configured provider.

### Typical Flow
`Chunking → **Embedding** → Vector Vault`
    """.format(
        provider=settings.default_embedding_provider,
        model=settings.default_embedding_model,
        dims=settings.embedding_dimension,
    ),
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "embeddings", "description": "Text embedding generation"},
        {"name": "health", "description": "Service health"},
    ],
)

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_pipeline = EmbeddingPipeline()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/embed", response_model=EmbedResponse, tags=["embeddings"],
          summary="Generate embeddings for a list of texts")
async def embed(request: EmbedRequest = Body(...)):
    """
    Embed a list of texts using the configured provider.
    Returns a vector per input text, the provider used, and dimension count.
    """
    try:
        embeddings, provider, model = await _pipeline.embed(
            texts=request.texts,
            provider=request.provider,
            batch_size=request.batch_size,
        )
        return EmbedResponse(
            embeddings=embeddings,
            provider=provider,
            model=model,
            dimensions=len(embeddings[0]) if embeddings else 0,
            count=len(embeddings),
        )
    except Exception as exc:
        logger.error("embedding_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/embed/info", tags=["embeddings"],
         summary="Get current embedding provider info")
async def embed_info():
    """Returns the active embedding provider, model, and dimension count."""
    return {
        "provider":   settings.default_embedding_provider,
        "model":      settings.default_embedding_model,
        "dimensions": settings.embedding_dimension,
        "batch_size": settings.embedding_batch_size,
    }


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "embedding_service",
            "version": APP_VERSION, "provider": settings.default_embedding_provider}
