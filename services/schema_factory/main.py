"""
DATA ENGINE — Schema Factory Service
Phase 2: Generates GEO-optimised Schema.org JSON-LD structured data.
Builds FAQPage, Article, Organisation schemas with sameAs authority links.

Swagger UI: http://localhost:8009/docs
ReDoc:      http://localhost:8009/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION, SchemaType
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.schema_factory.schema_builder import SchemaBuilder, GEOEntity
from services.schema_factory.llm_factory import LLMSchemaFactory

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class EntityInput(BaseModel):
    name: str
    entity_type: str = "Organisation"
    wikidata_id: str = ""
    wikipedia_slug: str = ""
    linkedin_slug: str = ""
    custom_same_as: list[str] = Field(default_factory=list)
    description: str = ""
    url: str = ""


class SchemaRequest(BaseModel):
    document_id: str
    tenant_id: str
    content: str = Field(..., min_length=20, description="Document text to generate schema from")
    schema_type: str = Field(default="Article", description="Schema.org type")
    entities: list[EntityInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    missing_topics: list[str] = Field(default_factory=list)

    model_config = {"json_schema_extra": {"example": {
        "document_id": "uuid",
        "tenant_id": "uuid",
        "content": "OpenAI is an AI research company that developed GPT-4...",
        "schema_type": "Article",
        "entities": [{"name": "OpenAI", "entity_type": "Organisation",
                      "wikipedia_slug": "OpenAI", "linkedin_slug": "openai"}],
        "metadata": {"title": "OpenAI Overview", "url": "https://example.com/openai"},
    }}}


class SchemaResponse(BaseModel):
    jsonld: dict[str, Any]
    script_tag: str
    citation_score: float
    same_as_urls: list[str]
    first_sentence: str
    schema_type: str


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("schema_factory_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("schema_factory_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Schema Factory",
    description="""
## Schema Factory

Generates GEO-optimised Schema.org JSON-LD structured data for AI engine citation.

### Schema Types Supported
`Article` · `FAQPage` · `HowTo` · `Product` · `Organisation` · `WebPage` · `Dataset` · `SoftwareApplication`

### GEO Optimisation
- **First Sentence Rule** — enforces direct, machine-scannable upfront answers
- **sameAs Authority Links** — Wikidata, Wikipedia, LinkedIn, Crunchbase
- **Citation Score** — 0–1 score predicting LLM citation likelihood
- **Entity Trust Signals** — nested entity relationships for knowledge graph matching

### Output
Returns ready-to-embed `<script type="application/ld+json">` tag for your HTML.
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "schemas", "description": "Schema generation"},
        {"name": "health", "description": "Service health"},
    ],
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_builder = SchemaBuilder()
_factory = LLMSchemaFactory()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/schemas/generate", response_model=SchemaResponse,
          tags=["schemas"], summary="Generate GEO-optimised JSON-LD schema")
async def generate_schema(request: SchemaRequest = Body(...)):
    """
    Build a complete Schema.org JSON-LD bundle from document content.
    Returns the JSON-LD, a ready-to-embed script tag, and citation readiness score.
    """
    try:
        entities = [GEOEntity(**e.model_dump()) for e in request.entities]
        bundle = await _builder.build(
            document_id=request.document_id,
            tenant_id=request.tenant_id,
            content=request.content,
            schema_type=request.schema_type,
            entities=entities,
            metadata=request.metadata,
            missing_topics=request.missing_topics,
        )
        return SchemaResponse(
            jsonld=bundle.jsonld,
            script_tag=bundle.script_tag,
            citation_score=bundle.citation_score,
            same_as_urls=bundle.same_as_urls,
            first_sentence=bundle.first_sentence,
            schema_type=bundle.schema_type,
        )
    except Exception as exc:
        logger.error("schema_generation_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/schemas/types", tags=["schemas"],
         summary="List supported Schema.org types")
async def list_schema_types():
    """Returns all supported Schema.org types and their GEO optimisation level."""
    return {
        "types": [
            {"type": "FAQPage",       "geo_value": "highest", "description": "Q&A pairs — most cited by AI engines"},
            {"type": "HowTo",         "geo_value": "high",    "description": "Step-by-step instructions"},
            {"type": "Article",       "geo_value": "medium",  "description": "General article content"},
            {"type": "Organisation",  "geo_value": "high",    "description": "Company/brand with sameAs links"},
            {"type": "Product",       "geo_value": "medium",  "description": "Product with offers and reviews"},
            {"type": "WebPage",       "geo_value": "low",     "description": "Generic webpage"},
            {"type": "Dataset",       "geo_value": "medium",  "description": "Structured dataset"},
            {"type": "SoftwareApplication", "geo_value": "medium", "description": "Software/app"},
        ]
    }


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "schema_factory", "version": APP_VERSION}
