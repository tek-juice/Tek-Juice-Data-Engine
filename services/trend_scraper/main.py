"""
DATA ENGINE — Trend Scraper Service
Phase 2: Global trend monitoring across Google, Bing, News,
and 9 social media platforms.

Swagger UI : http://localhost:8006/docs
ReDoc      : http://localhost:8006/redoc
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from configs.constants import API_PREFIX, APP_VERSION
from configs.database import dispose_db, init_db
from configs.settings import get_settings
from services.trend_scraper.scheduler import TrendScraperScheduler
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.request_id import RequestIDMiddleware

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    sources: list[str] | None = Field(
        default=None,
        description="Sources to scrape: google, bing, news, social_media. Defaults to all.",
    )
    queries: list[str] | None = Field(
        default=None,
        description="Search queries. Defaults to configured defaults.",
    )

    model_config = {"json_schema_extra": {"example": {
        "sources": ["bing", "social_media"],
        "queries": ["AI trends 2025", "vector database technology"],
    }}}


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("trend_scraper_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("trend_scraper_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Trend Scraper",
    description="""
## Trend Scraper Service

Monitors global trend signals from 4 source categories and 9 social platforms.

### Sources
| Source | Method | Auth required |
|--------|--------|---------------|
| **Google** | Custom Search API + geo-rotation | `GOOGLE_SEARCH_API_KEY` |
| **Bing** | ScraperAPI (no Azure needed) | `SCRAPER_API_KEY` |
| **News** | RSS aggregation | None |
| **Social Media** | 9 platform APIs (concurrent) | Per-platform keys |

### Social Platforms
`Hacker News` · `Reddit` · `X/Twitter` · `Facebook` · `Instagram`
`TikTok` · `Snapchat` · `YouTube` · `LinkedIn`

### Anti-blocking
- Rotating user agents, rate limiting, jitter delays
- ScraperAPI proxy rotation for Bing and direct scrapes
- Playwright headless browser for SPA/Cloudflare-protected pages
- Pydantic validation on every platform payload with dead-letter queue fallback
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "scraping",  "description": "Trigger and manage scrape runs"},
        {"name": "platforms", "description": "Platform status and configuration"},
        {"name": "health",    "description": "Service health"},
    ],
)

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

_scheduler = TrendScraperScheduler()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(
    f"{API_PREFIX}/scrape/run",
    tags=["scraping"],
    summary="Trigger a scraping run",
)
async def run_scrape(request: ScrapeRequest = Body(...)):
    """
    Trigger a scraping run across the specified sources and queries.
    All sources run concurrently. Returns counts per source and elapsed time.
    """
    try:
        result = await _scheduler.run(
            sources=request.sources,
            queries=request.queries,
        )
        return result
    except Exception as exc:
        logger.error("scrape_run_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    f"{API_PREFIX}/scrape/platforms",
    tags=["platforms"],
    summary="List all supported platforms and their sub-sources",
)
async def list_platforms():
    """Returns all supported scraping sources and their platform breakdown."""
    return {
        "platforms": TrendScraperScheduler.available_platforms(),
        "configured": {
            "google":   bool(settings.google_search_api_key and settings.google_search_cx),
            "bing":     bool(settings.scraper_api_key or settings.bing_search_api_key),
            "news":     True,
            "reddit":   bool(settings.reddit_client_id),
            "twitter":  bool(settings.twitter_bearer_token),
            "facebook": bool(settings.facebook_access_token),
            "instagram": bool(settings.instagram_access_token),
            "tiktok":   bool(settings.tiktok_client_key),
            "snapchat": bool(settings.snapchat_access_token),
            "youtube":  bool(settings.youtube_api_key),
            "hackernews": True,
        },
    }


@app.get(
    f"{API_PREFIX}/scrape/health/platforms",
    tags=["platforms"],
    summary="Get per-platform scraper health from dead-letter queue stats",
)
async def platform_health():
    """Returns health status for all scrapers from the scraper_health table."""
    try:
        from services.trend_scraper.validators.dead_letter import get_scraper_health_summary
        summary = await get_scraper_health_summary()
        return {"platforms": summary}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(
    f"{API_PREFIX}/scrape/dead-letter",
    tags=["scraping"],
    summary="View recent dead-letter queue entries",
)
async def dead_letter_queue(
    platform: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """View recent failed scraper payloads from the dead-letter queue."""
    try:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            q = """
                SELECT id, platform, source, query, error_type,
                       error_detail, retry_count, resolved, created_at
                FROM scraper_dead_letter_queue
                WHERE (:platform IS NULL OR platform = :platform)
                  AND resolved = FALSE
                ORDER BY created_at DESC
                LIMIT :lim
            """
            rows = await session.execute(
                text(q), {"platform": platform, "lim": limit}
            )
            items = [dict(r._mapping) for r in rows.fetchall()]
        return {"count": len(items), "items": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "trend_scraper", "version": APP_VERSION}
