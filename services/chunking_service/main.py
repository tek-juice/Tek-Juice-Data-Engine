"""
DATA ENGINE — Chunking Service
Phase 1: Splits documents into optimally-sized chunks for embedding.
Supports token-based and semantic chunking strategies.

Swagger UI: http://localhost:8002/docs
ReDoc:      http://localhost:8002/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.chunking_service.token_chunker import TokenChunker
from services.chunking_service.semantic_chunker import SemanticChunker

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Request / Response Models ─────────────────────────────────────────────────

class ChunkRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to chunk")
    strategy: Literal["token", "semantic"] = Field("token", description="Chunking strategy")
    min_tokens: int = Field(default=256, ge=64, le=512)
    max_tokens: int = Field(default=512, ge=128, le=1024)
    overlap_tokens: int = Field(default=64, ge=0, le=256)
    document_id: str | None = Field(None, description="Optional document UUID for metadata")

    model_config = {"json_schema_extra": {"example": {
        "text": "Artificial intelligence is transforming how businesses operate...",
        "strategy": "token",
        "min_tokens": 256,
        "max_tokens": 512,
        "overlap_tokens": 64,
    }}}


class Chunk(BaseModel):
    index: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    strategy: str


class ChunkResponse(BaseModel):
    document_id: str | None
    strategy: str
    total_chunks: int
    chunks: list[Chunk]


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("chunking_service_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("chunking_service_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Chunking Service",
    description="""
## Chunking Service

Splits documents into optimally-sized text chunks for downstream embedding.

### Strategies
- **token** — Fixed-size token windows with configurable overlap. Fast and deterministic.
- **semantic** — Splits at semantic boundaries using sentence embeddings. Better for RAG retrieval.

### Typical Flow
`Ingestion → **Chunking** → Embedding → Vector Vault`
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "chunking", "description": "Text chunking operations"},
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

@app.post(f"{API_PREFIX}/chunk", response_model=ChunkResponse, tags=["chunking"],
          summary="Chunk text into segments")
async def chunk_text(request: ChunkRequest = Body(...)):
    """
    Split text into chunks using the specified strategy.
    Returns all chunks with token counts and character positions.
    """
    try:
        if request.strategy == "semantic":
            chunker = SemanticChunker(
                min_tokens=request.min_tokens,
                max_tokens=request.max_tokens,
            )
        else:
            chunker = TokenChunker(
                min_tokens=request.min_tokens,
                max_tokens=request.max_tokens,
                overlap_tokens=request.overlap_tokens,
            )

        raw_chunks = chunker.chunk(request.text)
        chunks = [
            Chunk(
                index=i,
                text=c.get("text", ""),
                token_count=c.get("token_count", 0),
                char_start=c.get("char_start", 0),
                char_end=c.get("char_end", 0),
                strategy=request.strategy,
            )
            for i, c in enumerate(raw_chunks)
        ]
        return ChunkResponse(
            document_id=request.document_id,
            strategy=request.strategy,
            total_chunks=len(chunks),
            chunks=chunks,
        )
    except Exception as exc:
        logger.error("chunking_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "chunking_service", "version": APP_VERSION}
