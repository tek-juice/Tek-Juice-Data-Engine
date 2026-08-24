"""
DATA ENGINE — AEO Engine Service
Phase 4: Answer Engine Optimisation — optimises content for direct extraction
by Google Featured Snippets, Bing Answer Boxes, Perplexity, ChatGPT, Siri,
Alexa, People Also Ask boxes, and AI Overviews.

AEO sits alongside SEO (keyword ranking) and GEO (generative engine entity
authority) to complete the triple-optimisation stack:
  SEO  → rank in blue-link search results
  GEO  → get cited inside AI-generated answers
  AEO  → win position-zero answer boxes and voice responses

Swagger UI : http://localhost:8014/docs
ReDoc      : http://localhost:8014/redoc
"""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Body, Query
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from configs.constants import API_PREFIX, APP_VERSION
from configs.database import dispose_db, init_db
from configs.settings import get_settings
from services.aeo_engine.aeo_optimizer import AEOBundle, AEOOptimiser
from services.aeo_engine.answer_scorer import AnswerScorer
from services.aeo_engine.featured_snippet import FeaturedSnippetOptimiser
from services.aeo_engine.position_zero import PositionZeroAnalyser
from services.aeo_engine.question_mapper import QuestionMapper
from services.aeo_engine.voice_search import VoiceSearchOptimiser
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.request_id import RequestIDMiddleware

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Request / Response models ─────────────────────────────────────────────────

class AEORequest(BaseModel):
    document_id: str = Field(..., description="Source document UUID")
    tenant_id: str = Field(..., description="Tenant UUID")
    content: str = Field(..., min_length=50, description="Full plain-text document content")
    title: str = Field(default="", description="Page title")
    url: str = Field(default="", description="Page URL")
    target_questions: list[str] = Field(
        default_factory=list,
        description="Specific questions to optimise for",
    )
    engine_targets: list[str] = Field(
        default_factory=list,
        description="Answer engines to target. Leave empty to target all.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "tenant_id":   "550e8400-e29b-41d4-a716-446655440001",
                "title":       "What is a Vector Database?",
                "url":         "https://example.com/vector-databases",
                "content": (
                    "A vector database is a specialised storage system that indexes and "
                    "retrieves high-dimensional vectors for semantic similarity search. "
                    "Unlike traditional relational databases that match exact values, "
                    "vector databases use approximate nearest-neighbour (ANN) algorithms "
                    "to find conceptually similar items. They power AI applications including "
                    "recommendation engines, semantic search, and RAG pipelines."
                ),
                "target_questions": [
                    "What is a vector database?",
                    "How does a vector database work?",
                    "What are vector databases used for?",
                ],
                "engine_targets": [
                    "google_featured_snippet",
                    "google_ai_overview",
                    "perplexity",
                    "chatgpt",
                ],
            }
        }
    }


class AEOScoreBreakdown(BaseModel):
    overall_aeo_score: float
    answer_readiness: float
    voice_readiness: float
    snippet_readiness: float
    pz_readiness: float


class AEOResponse(BaseModel):
    document_id: str
    tenant_id: str
    scores: AEOScoreBreakdown
    snippet_ready: bool
    spoken_answer: str
    optimised_intro: str
    pz_opportunities: int
    questions_mapped: int
    questions_answered: int
    priority_actions: list[str]
    quick_wins: list[str]
    recommendations: list[str]
    engine_targets: list[str]
    analysed_at: str
    word_count: int


class AnswerScoreRequest(BaseModel):
    content: str = Field(..., min_length=10)
    question: str = Field(default="", description="The question this content answers")

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": (
                    "A vector database stores embeddings as high-dimensional numerical "
                    "vectors and retrieves them using approximate nearest-neighbour search. "
                    "It enables sub-millisecond semantic similarity lookups across millions "
                    "of documents, making it essential for AI-powered search and RAG systems."
                ),
                "question": "What is a vector database?",
            }
        }
    }


class QuestionMapRequest(BaseModel):
    content: str = Field(..., min_length=50)
    document_id: str = Field(default="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": (
                    "## What is semantic search?\n\n"
                    "Semantic search understands the meaning behind a query rather than "
                    "matching exact keywords. It uses vector embeddings to find conceptually "
                    "similar results even when the exact words differ.\n\n"
                    "## How does it work?\n\n"
                    "Text is converted to numerical vectors via an embedding model. "
                    "Queries are embedded the same way and compared using cosine similarity."
                ),
                "document_id": "uuid",
            }
        }
    }


class SnippetRequest(BaseModel):
    content: str = Field(..., min_length=50)

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": (
                    "Semantic search is a search technique that understands user intent "
                    "and contextual meaning rather than matching keywords literally. "
                    "It uses machine learning embeddings to represent text as vectors "
                    "and finds results based on conceptual similarity."
                )
            }
        }
    }


class VoiceSearchRequest(BaseModel):
    content: str = Field(..., min_length=20)
    question: str = Field(default="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": (
                    "You can use a vector database by connecting it to your application "
                    "via an SDK or REST API. First, generate embeddings for your data "
                    "using an embedding model like Gemini or OpenAI. Then store the vectors "
                    "and run similarity queries against them."
                ),
                "question": "How do I use a vector database?",
            }
        }
    }


class PositionZeroRequest(BaseModel):
    content: str = Field(..., min_length=50)
    title: str = Field(default="")
    url: str = Field(default="")

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Vector Database Guide",
                "url": "https://example.com/vector-db",
                "content": (
                    "## What is a vector database?\n\n"
                    "A vector database is a purpose-built system for storing and querying "
                    "high-dimensional vector embeddings. It enables semantic search across "
                    "large document collections at sub-millisecond latency.\n\n"
                    "## Top vector databases in 2025\n\n"
                    "- Pinecone\n- Weaviate\n- Qdrant\n- pgvector\n- Chroma\n\n"
                    "## How to choose a vector database\n\n"
                    "1. Define your scale requirements (number of vectors)\n"
                    "2. Choose between managed vs self-hosted\n"
                    "3. Evaluate indexing algorithms (HNSW vs IVF)\n"
                    "4. Compare latency benchmarks\n"
                    "5. Check SDK support for your language"
                ),
            }
        }
    }


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("aeo_engine_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("aeo_engine_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — AEO Engine",
    description="""
## AEO Engine (Answer Engine Optimisation)

Optimises content for direct extraction by answer engines — completing the
**triple-optimisation stack** alongside SEO and GEO.

---

### The Triple Stack

| Engine | Goal | Signal |
|--------|------|--------|
| **SEO** | Rank in blue-link results | Keywords, backlinks, technical |
| **GEO** | Get cited in AI answers | Entity authority, sameAs links |
| **AEO** | Win answer boxes & voice | Direct answers, snippet structure |

---

### What AEO Targets

- **Google Featured Snippets** — paragraph (40–60w), list (4–8 items), table, steps
- **Google AI Overviews** — structured, citable, entity-rich content
- **Bing Answer Boxes** — short factual answers (< 30 words)
- **People Also Ask (PAA)** — 4–8 word question blocks
- **Voice Search** — Siri, Alexa, Google Assistant (20–30 word spoken answers)
- **Perplexity / ChatGPT** — verifiable, sourced, direct answers
- **Knowledge Panels** — entity signals with authority links

---

### AEO Score Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Answer Readiness | 30% | Directness, completeness, verifiability |
| Snippet Readiness | 25% | Featured snippet structure and length |
| Position-Zero | 25% | All answer box types combined |
| Voice Readiness | 20% | Conversational, 20–30 word spoken answer |

---

### Typical Flow

`Ingestion → Chunking → Embedding → SEO → GEO → **AEO** → Dashboard`
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "aeo",
            "description": "Full AEO analysis — all modules combined",
        },
        {
            "name": "answer-scoring",
            "description": "Score a content block as a direct answer",
        },
        {
            "name": "questions",
            "description": "Extract and map questions from content",
        },
        {
            "name": "featured-snippets",
            "description": "Identify and optimise featured snippet candidates",
        },
        {
            "name": "voice-search",
            "description": "Optimise content for voice search and smart speakers",
        },
        {
            "name": "position-zero",
            "description": "Analyse position-zero opportunities across all answer box types",
        },
        {
            "name": "health",
            "description": "Service health check",
        },
    ],
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.gateway_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

_optimiser   = AEOOptimiser()
_a_scorer    = AnswerScorer()
_q_mapper    = QuestionMapper()
_snippet_opt = FeaturedSnippetOptimiser()
_voice_opt   = VoiceSearchOptimiser()
_pz_analyser = PositionZeroAnalyser()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(
    f"{API_PREFIX}/aeo/analyze",
    response_model=AEOResponse,
    tags=["aeo"],
    summary="Run full AEO analysis",
    response_description="Complete AEO bundle with scores, action plan, and optimised content",
)
async def analyze_aeo(request: AEORequest = Body(...)):
    """
    Run the complete AEO pipeline on a document.

    Executes all five AEO modules concurrently:
    - **Answer Scorer** — directness, completeness, verifiability
    - **Question Mapper** — explicit & implicit question detection
    - **Featured Snippet Optimiser** — paragraph, list, table, steps
    - **Voice Search Optimiser** — 20–30 word spoken answer
    - **Position-Zero Analyser** — all answer box types

    Returns a composite score, priority action plan, quick wins,
    and an optimised intro paragraph ready for immediate use.
    """
    try:
        bundle: AEOBundle = await _optimiser.optimise(
            document_id=request.document_id,
            tenant_id=request.tenant_id,
            content=request.content,
            title=request.title,
            url=request.url,
            target_questions=request.target_questions or None,
            engine_targets=request.engine_targets or None,
        )
        return AEOResponse(
            document_id=bundle.document_id,
            tenant_id=bundle.tenant_id,
            scores=AEOScoreBreakdown(
                overall_aeo_score=bundle.overall_aeo_score,
                answer_readiness=bundle.answer_readiness,
                voice_readiness=bundle.voice_readiness,
                snippet_readiness=bundle.snippet_readiness,
                pz_readiness=bundle.pz_readiness,
            ),
            snippet_ready=bundle.featured_snippet.has_snippet_ready_content,
            spoken_answer=bundle.spoken_answer,
            optimised_intro=bundle.optimised_intro,
            pz_opportunities=bundle.position_zero.total_opportunities,
            questions_mapped=bundle.question_map.total_questions,
            questions_answered=bundle.question_map.answered_count,
            priority_actions=bundle.priority_actions,
            quick_wins=bundle.quick_wins,
            recommendations=bundle.all_recommendations,
            engine_targets=bundle.engine_targets,
            analysed_at=bundle.analysed_at,
            word_count=bundle.word_count,
        )
    except Exception as exc:
        logger.error("aeo_analysis_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/aeo/score-answer",
    tags=["answer-scoring"],
    summary="Score a content block as a direct answer",
    response_description="Answer quality score with dimensional breakdown",
)
async def score_answer(request: AnswerScoreRequest = Body(...)):
    """
    Score a single content block for answer engine extraction quality.

    Returns a 0–100 score across four dimensions:
    - **Directness** (25 pts) — starts with direct answer, no filler
    - **Completeness** (25 pts) — covers what/why/how/when/where/who
    - **Conciseness** (25 pts) — optimal length for snippet type
    - **Verifiability** (25 pts) — statistics, dates, named sources
    """
    try:
        result = _a_scorer.score(content=request.content, question=request.question)
        return {
            "overall_score":       result.overall_score,
            "directness_score":    result.directness_score,
            "completeness_score":  result.completeness_score,
            "conciseness_score":   result.conciseness_score,
            "verifiability_score": result.verifiability_score,
            "answer_type":         result.answer_type,
            "word_count":          result.word_count,
            "starts_directly":     result.starts_directly,
            "has_filler_opener":   result.has_filler_opener,
            "snippet_ready":       result.snippet_ready,
            "has_statistics":      result.has_statistics,
            "has_named_sources":   result.has_named_sources,
            "completeness_signals": result.completeness_signals,
            "issues":              result.issues,
            "recommendations":     result.recommendations,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/aeo/map-questions",
    tags=["questions"],
    summary="Extract and classify questions from content",
    response_description="All detected questions with intent classification and coverage score",
)
async def map_questions(request: QuestionMapRequest = Body(...)):
    """
    Extract all explicit and implicit questions from content and
    classify each by intent (definitional, procedural, causal, etc.).

    Also checks whether each question has a direct answer in the content
    and returns an answer coverage score (0–1).
    """
    try:
        result = _q_mapper.map(content=request.content, document_id=request.document_id)
        return {
            "total_questions":      result.total_questions,
            "answered_count":       result.answered_count,
            "unanswered_count":     result.unanswered_count,
            "coverage_score":       result.coverage_score,
            "voice_search_count":   result.voice_search_count,
            "long_tail_count":      result.long_tail_count,
            "intent_distribution":  result.intent_distribution,
            "explicit_questions": [
                {
                    "text":             q.text,
                    "intent":           q.intent,
                    "is_voice_search":  q.is_voice_search,
                    "is_long_tail":     q.is_long_tail,
                    "has_direct_answer": q.has_direct_answer,
                    "answer_excerpt":   q.answer_excerpt,
                    "answer_quality":   q.answer_quality,
                }
                for q in result.explicit_questions
            ],
            "implicit_questions": [
                {
                    "text":             q.text,
                    "intent":           q.intent,
                    "is_voice_search":  q.is_voice_search,
                    "has_direct_answer": q.has_direct_answer,
                    "answer_quality":   q.answer_quality,
                }
                for q in result.implicit_questions
            ],
            "recommendations": result.recommendations,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/aeo/featured-snippet",
    tags=["featured-snippets"],
    summary="Identify and score featured snippet candidates",
    response_description="Ranked snippet candidates with optimised versions",
)
async def analyse_snippets(request: SnippetRequest = Body(...)):
    """
    Scan content for featured snippet candidates and return them ranked
    by snippet score. Each candidate includes an optimised version
    trimmed to the ideal length for its snippet type.

    Snippet types detected: paragraph · list · table · steps · definition
    """
    try:
        result = _snippet_opt.analyse(content=request.content)
        return {
            "has_snippet_ready_content":  result.has_snippet_ready_content,
            "overall_snippet_score":      result.overall_snippet_score,
            "snippet_type_distribution":  result.snippet_type_distribution,
            "best_candidate": {
                "text":                    result.best_candidate.text,
                "snippet_type":            result.best_candidate.snippet_type,
                "word_count":              result.best_candidate.word_count,
                "snippet_score":           result.best_candidate.snippet_score,
                "starts_with_direct_answer": result.best_candidate.starts_with_direct_answer,
                "has_trigger_phrase":      result.best_candidate.has_trigger_phrase,
                "readability_score":       result.best_candidate.readability_score,
                "optimised_text":          result.best_candidate.optimised_text,
                "issues":                  result.best_candidate.issues,
            } if result.best_candidate else None,
            "all_candidates": [
                {
                    "snippet_type":   c.snippet_type,
                    "word_count":     c.word_count,
                    "snippet_score":  c.snippet_score,
                    "optimised_text": c.optimised_text,
                }
                for c in result.all_candidates
            ],
            "recommendations": result.page_level_recommendations,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/aeo/voice-search",
    tags=["voice-search"],
    summary="Optimise content for voice search and smart speakers",
    response_description="Voice search score with spoken answer and optimised version",
)
async def analyse_voice(request: VoiceSearchRequest = Body(...)):
    """
    Score content for voice search extraction quality and return
    a 20–30 word spoken answer ready for Siri, Alexa, and Google Assistant.

    Voice search optimisation covers:
    - Conversational language (you/your/we)
    - Optimal spoken response length (20–30 words)
    - Question pattern alignment
    - Local intent signals
    """
    try:
        result = _voice_opt.analyse(content=request.content, question=request.question)
        return {
            "overall_score":              result.overall_score,
            "conversational_score":       result.conversational_score,
            "answer_length_score":        result.answer_length_score,
            "question_alignment_score":   result.question_alignment_score,
            "local_intent_score":         result.local_intent_score,
            "word_count":                 result.word_count,
            "is_conversational":          result.is_conversational,
            "has_local_intent":           result.has_local_intent,
            "has_filler_words":           result.has_filler_words,
            "detected_voice_patterns":    result.detected_voice_patterns,
            "spoken_answer":              result.spoken_answer,
            "optimised_for_voice":        result.optimised_for_voice,
            "recommendations":            result.recommendations,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/aeo/position-zero",
    tags=["position-zero"],
    summary="Analyse all position-zero opportunities",
    response_description="Position-zero opportunities ranked by readiness",
)
async def analyse_position_zero(request: PositionZeroRequest = Body(...)):
    """
    Identify all position-zero opportunities in content including:

    - **Featured Snippets** (paragraph, list, table, steps)
    - **Answer Boxes** — short factual answers
    - **Knowledge Panel** — entity authority signals
    - **People Also Ask** — PAA question extraction

    Returns each opportunity ranked by readiness score with
    the specific changes needed to win that position.
    """
    try:
        result = _pz_analyser.analyse(
            content=request.content,
            title=request.title,
            url=request.url,
        )
        return {
            "overall_pz_score":          result.overall_pz_score,
            "total_opportunities":        result.total_opportunities,
            "high_readiness_count":       result.high_readiness_count,
            "type_breakdown":             result.type_breakdown,
            "people_also_ask_questions":  result.people_also_ask_questions,
            "knowledge_panel_signals":    result.knowledge_panel_signals,
            "best_opportunity": {
                "position_zero_type":   result.best_opportunity.position_zero_type,
                "readiness_score":      result.best_opportunity.readiness_score,
                "content_block":        result.best_opportunity.content_block[:300],
                "required_changes":     result.best_opportunity.required_changes,
                "trigger_found":        result.best_opportunity.trigger_found,
            } if result.best_opportunity else None,
            "all_opportunities": [
                {
                    "position_zero_type": o.position_zero_type,
                    "readiness_score":    o.readiness_score,
                    "required_changes":   o.required_changes,
                }
                for o in result.opportunities
            ],
            "recommendations": result.recommendations,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    f"{API_PREFIX}/aeo/history/{{document_id}}",
    tags=["aeo"],
    summary="Get AEO analysis history for a document",
)
async def aeo_history(
    document_id: str,
    tenant_id: str = Query(..., description="Tenant UUID"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Return previous AEO analysis results for a document, newest first."""
    try:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text as sa_text

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                sa_text(
                    """
                    SELECT id, overall_aeo_score, answer_readiness,
                           voice_readiness, snippet_readiness, pz_readiness,
                           snippet_ready, pz_opportunities, questions_answered,
                           analysed_at
                    FROM aeo_optimisation_results
                    WHERE document_id = :doc AND tenant_id = :ten
                    ORDER BY analysed_at DESC
                    LIMIT :lim
                    """
                ),
                {"doc": document_id, "ten": tenant_id, "lim": limit},
            )
            history = [dict(r._mapping) for r in rows.fetchall()]
        return {"document_id": document_id, "history": history}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    f"{API_PREFIX}/aeo/engine-targets",
    tags=["aeo"],
    summary="List all supported answer engine targets",
)
async def list_engine_targets():
    """Returns all answer engines the AEO Engine can optimise for."""
    return {
        "engine_targets": [
            {"id": "google_featured_snippet", "type": "snippet",
             "description": "Google paragraph, list, table, steps snippets"},
            {"id": "google_ai_overview",      "type": "ai",
             "description": "Google Search Generative Experience (SGE) overviews"},
            {"id": "bing_answer_box",         "type": "answer_box",
             "description": "Bing direct answer boxes and Copilot answers"},
            {"id": "perplexity",              "type": "ai",
             "description": "Perplexity answer cards with source citations"},
            {"id": "chatgpt",                 "type": "ai",
             "description": "ChatGPT web search and browse mode citations"},
            {"id": "siri",                    "type": "voice",
             "description": "Apple Siri spoken responses (20–30 words)"},
            {"id": "alexa",                   "type": "voice",
             "description": "Amazon Alexa skill and flash briefing answers"},
            {"id": "google_assistant",        "type": "voice",
             "description": "Google Assistant voice responses"},
            {"id": "people_also_ask",         "type": "paa",
             "description": "Google People Also Ask expandable answer boxes"},
            {"id": "knowledge_panel",         "type": "panel",
             "description": "Google/Bing Knowledge Panel entity cards"},
        ]
    }


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
)
async def health():
    return {
        "status":  "healthy",
        "service": "aeo_engine",
        "version": APP_VERSION,
    }
