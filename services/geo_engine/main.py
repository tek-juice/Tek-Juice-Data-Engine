"""
DATA ENGINE — GEO Engine Service
Phase 4: Generative Engine Optimisation — entity extraction, knowledge graph
construction, LLM visibility scoring, and citation readiness analysis.
Also hosts LEO (LLM Engine Optimisation) public inventory/pricing endpoints
and VSEO (Visual SEO) multi-modal image/video optimisation endpoints.

Swagger UI: http://localhost:8013/docs
ReDoc:      http://localhost:8013/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body, Query
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
from services.geo_engine.citations import CitationReadinessAnalyser
from services.geo_engine.leo_api import LEOBuilder
from services.geo_engine.vseo_optimizer import VseoOptimiser, ImageAsset, VideoAsset, TranscriptSegment

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


# ── LEO Models ────────────────────────────────────────────────────────────────

class LEOOfferRequest(BaseModel):
    price: float
    currency: str = "USD"
    availability: str = "in_stock"
    price_valid_until: str = ""
    url: str = ""
    seller_name: str = ""


class LEOItemRequest(BaseModel):
    id: str
    name: str
    description: str
    brand: str = ""
    sku: str = ""
    gtin: str = ""
    category: str = ""
    url: str = ""
    image_url: str = ""
    tags: list[str] = Field(default_factory=list)
    ai_summary: str = ""
    offers: list[LEOOfferRequest] = Field(default_factory=list)

    model_config = {"json_schema_extra": {"example": {
        "id": "prod-001", "name": "AI Inventory Manager", "description": "Real-time stock sync across channels.",
        "brand": "Tek Juice", "sku": "TJ-AIM-001", "category": "Software",
        "url": "https://example.com/products/ai-inventory",
        "offers": [{"price": 99.00, "currency": "USD", "availability": "in_stock"}],
    }}}


# ── VSEO Models ───────────────────────────────────────────────────────────────

class ImageRequest(BaseModel):
    url: str
    alt_text: str = ""
    caption: str = ""
    filename: str = ""
    width: int = 0
    height: int = 0
    file_size_kb: int = 0
    content_url: str = ""
    license_url: str = ""
    author: str = ""
    ai_description: str = ""


class TranscriptSegmentRequest(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class VideoRequest(BaseModel):
    url: str
    name: str = ""
    description: str = ""
    thumbnail_url: str = ""
    duration_iso: str = ""
    duration_seconds: int = 0
    upload_date: str = ""
    author: str = ""
    embed_url: str = ""
    transcript_segments: list[TranscriptSegmentRequest] = Field(default_factory=list)


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
        {"name": "geo",    "description": "GEO analysis and optimisation"},
        {"name": "leo",    "description": "LEO public inventory/pricing endpoints for AI engine consumption"},
        {"name": "vseo",   "description": "VSEO multi-modal image/video optimisation"},
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
_citations = CitationReadinessAnalyser()
_leo_builder = LEOBuilder()
_vseo = VseoOptimiser()


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
        citation_result = _citations.analyse(request.content)
        visibility = _scorer.score(
            content=request.content,
            entity_count=len(entities),
            citation_score=citation_result.overall_score,
            context_richness=0.0,
            target_models=request.target_models,
        )
        return GEOResult(
            llm_visibility_score=visibility.overall_score,
            entity_coverage=visibility.entity_coverage_score,
            citation_readiness=citation_result.overall_score >= 50.0,
            context_richness_score=visibility.semantic_clarity_score,
            extracted_entities=[e.to_dict() if hasattr(e, "to_dict") else vars(e) for e in entities],
            recommendations=citation_result.recommendations,
            optimised_content=None,
        )
    except Exception as exc:
        logger.error("geo_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "geo_engine", "version": APP_VERSION}


# ── LEO Routes ────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/leo/items", tags=["leo"],
          summary="Build a LEO-compliant inventory item response with embedded Schema.org markup")
async def leo_build_item(request: LEOItemRequest = Body(...)):
    """
    Build a single LEO (LLM Engine Optimisation) inventory item.
    Returns raw data fields AND embedded Schema.org JSON-LD so AI engines
    can consume structured product data directly without HTML parsing.
    """
    try:
        item = _leo_builder.build_item(
            id=request.id,
            name=request.name,
            description=request.description,
            offers=[o.model_dump() for o in request.offers],
            brand=request.brand,
            sku=request.sku,
            gtin=request.gtin,
            category=request.category,
            url=request.url,
            image_url=request.image_url,
            tags=request.tags,
            ai_summary=request.ai_summary,
        )
        return item.to_leo_dict()
    except Exception as exc:
        logger.error("leo_build_item_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/leo/feed", tags=["leo"],
          summary="Build a paginated LEO inventory feed with Schema.org ItemList")
async def leo_build_feed(
    items: list[LEOItemRequest] = Body(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_name: str = Query(default=""),
    feed_url: str = Query(default=""),
):
    """
    Build a paginated LEO inventory feed.
    Returns per-item Schema.org Product blocks and a top-level ItemList block.
    """
    try:
        built_items = [
            _leo_builder.build_item(
                id=r.id, name=r.name, description=r.description,
                offers=[o.model_dump() for o in r.offers],
                brand=r.brand, sku=r.sku, gtin=r.gtin,
                category=r.category, url=r.url, image_url=r.image_url,
                tags=r.tags, ai_summary=r.ai_summary,
            )
            for r in items
        ]
        feed = _leo_builder.build_feed(
            items=built_items,
            total=len(built_items),
            page=page,
            page_size=page_size,
            tenant_name=tenant_name,
            feed_url=feed_url,
        )
        return feed.to_response()
    except Exception as exc:
        logger.error("leo_build_feed_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ── VSEO Routes ───────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/vseo/image", tags=["vseo"],
          summary="Score a single image for AI visual search optimisation")
async def vseo_score_image(request: ImageRequest = Body(...)):
    """
    Score an image for VSEO readiness.
    Returns dimension scores (alt text, caption, filename, technical),
    issues, recommendations, and a ready-to-embed Schema.org ImageObject.
    """
    try:
        asset = ImageAsset(
            url=request.url,
            alt_text=request.alt_text,
            caption=request.caption,
            filename=request.filename,
            width=request.width,
            height=request.height,
            file_size_kb=request.file_size_kb,
            content_url=request.content_url,
            license_url=request.license_url,
            author=request.author,
            ai_description=request.ai_description,
        )
        result = _vseo.score_image(asset)
        return {
            "url":               result.url,
            "overall_score":     result.overall_score,
            "alt_text_score":    result.alt_text_score,
            "caption_score":     result.caption_score,
            "filename_score":    result.filename_score,
            "technical_score":   result.technical_score,
            "issues":            result.issues,
            "recommendations":   result.recommendations,
            "optimised_alt_text": result.optimised_alt_text,
            "schema_org":        result.schema_org,
        }
    except Exception as exc:
        logger.error("vseo_image_score_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/vseo/page-images", tags=["vseo"],
          summary="Score all images on a page for aggregate VSEO readiness")
async def vseo_score_page_images(images: list[ImageRequest] = Body(...)):
    """
    Score all images on a page.
    Returns a page-level average score, per-image breakdown, and deduplicated recommendations.
    """
    try:
        assets = [
            ImageAsset(
                url=r.url, alt_text=r.alt_text, caption=r.caption,
                filename=r.filename, width=r.width, height=r.height,
                file_size_kb=r.file_size_kb, content_url=r.content_url,
                license_url=r.license_url, author=r.author,
                ai_description=r.ai_description,
            )
            for r in images
        ]
        return _vseo.score_page_images(assets)
    except Exception as exc:
        logger.error("vseo_page_images_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/vseo/video", tags=["vseo"],
          summary="Score a video for AI video search optimisation")
async def vseo_score_video(request: VideoRequest = Body(...)):
    """
    Score a video for VSEO readiness.
    Returns dimension scores (metadata, transcript, technical),
    issues, recommendations, an AI summary, and a ready-to-embed VideoObject JSON-LD.
    """
    try:
        segments = [
            TranscriptSegment(
                start_seconds=s.start_seconds,
                end_seconds=s.end_seconds,
                text=s.text,
            )
            for s in request.transcript_segments
        ]
        asset = VideoAsset(
            url=request.url,
            name=request.name,
            description=request.description,
            thumbnail_url=request.thumbnail_url,
            duration_iso=request.duration_iso,
            duration_seconds=request.duration_seconds,
            upload_date=request.upload_date,
            author=request.author,
            embed_url=request.embed_url,
            transcript_segments=segments,
        )
        result = _vseo.score_video(asset)
        return {
            "url":              result.url,
            "overall_score":    result.overall_score,
            "metadata_score":   result.metadata_score,
            "transcript_score": result.transcript_score,
            "technical_score":  result.technical_score,
            "ai_summary":       result.ai_summary,
            "issues":           result.issues,
            "recommendations":  result.recommendations,
            "schema_org":       result.schema_org,
        }
    except Exception as exc:
        logger.error("vseo_video_score_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
