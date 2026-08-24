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
from prometheus_client import make_asgi_app
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

Monitors global trend signals across **5 source tiers** and **19+ channels**,
with a complete bypass layer for blocked or restricted social media platforms.

### Tier 1 — Official APIs (require credentials)
| Source | Method | Auth |
|--------|--------|------|
| **Google** | Custom Search API + geo-rotation | `GOOGLE_SEARCH_API_KEY` |
| **Bing** | ScraperAPI → Microsoft API → Direct HTML | `SCRAPER_API_KEY` |
| **News** | RSS aggregation | None |
| **Social** | 9 platform APIs concurrent | Per-platform keys |

### Tier 2 — Indirect / Public Channels (**no API keys required**)
These bypass all platform API blocks and rate limits automatically:

| Channel | Source | Bypass method |
|---------|--------|---------------|
| **Google Trends** | Daily breakout topics | Public suggest + dailytrends API |
| **Twitter/X** | Nitter RSS mirrors | RSS from nitter.net (no API key) |
| **Reddit** | Public JSON API | reddit.com/r/all.json (no OAuth) |
| **TikTok** | Web search API | TikTok web session (no app auth) |
| **YouTube** | Trending RSS | youtube.com/feeds (no API key) |
| **GitHub** | Trending repos | HTML scrape with fingerprint spoof |
| **Wikipedia** | Pageviews API | Wikimedia REST API (no auth) |
| **Medium** | Tag RSS | Public tag feeds (no auth) |
| **Instagram** | Public GraphQL | Public hashtag endpoint |

### Anti-blocking Engine (upgraded)
- **Browser fingerprint spoofing** — consistent UA + canvas + WebGL + audio
  fingerprints defeat bot detectors (Cloudflare, DataDome, PerimeterX)
- **Session warming** — persistent cookie jars simulate returning users
- **TLS fingerprint cycling** — HTTP/2 + cipher order rotation
- **Nitter mirror rotation** — 5 Nitter instances for Twitter/X data
- **Playwright stealth** — JS init script masks all automation indicators
- **ScraperAPI proxy rotation** — residential IPs for Bing + social scrapes
- **Gaussian jitter delays** — human-like timing histogram
- **Dead-letter queue** — captures all validation failures with auto-alerts
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "scraping",  "description": "Trigger and manage scrape runs"},
        {"name": "indirect",  "description": "Indirect/public signal channels (no API keys)"},
        {"name": "platforms", "description": "Platform status and configuration"},
        {"name": "health",    "description": "Service health"},
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
    f"{API_PREFIX}/scrape/trending",
    tags=["indirect"],
    summary="Fetch what's trending right now (no API keys required)",
)
async def fetch_trending_now():
    """
    Fetch currently trending content across all public/open channels.
    Requires zero API keys — uses Google Daily Trends, YouTube Trending RSS,
    Wikipedia Pageviews, and GitHub Trending as sources.
    Returns up to 60 trending items across all channels.
    """
    try:
        from services.trend_scraper.social_media.indirect_signals import IndirectSignalCollector
        collector = IndirectSignalCollector()
        items = await collector.fetch_trending_only(limit=60)
        return {"count": len(items), "items": items}
    except Exception as exc:
        logger.error("trending_now_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    f"{API_PREFIX}/scrape/indirect",
    tags=["indirect"],
    summary="Run indirect/public signal collection for a query",
)
async def run_indirect_scrape(request: ScrapeRequest = Body(...)):
    """
    Run all public/open-channel signal collection for the given queries.
    No API keys required. Runs 9 channels concurrently:
    Google Trends, Nitter/Twitter RSS, Reddit Public JSON, TikTok Web,
    YouTube Trending RSS, GitHub Trending, Wikipedia Pageviews, Medium RSS,
    Instagram Public GraphQL.
    """
    try:
        from services.trend_scraper.social_media.indirect_signals import IndirectSignalCollector
        collector = IndirectSignalCollector()
        queries   = request.queries or ["AI trends", "technology", "machine learning"]
        all_items: list[dict] = []
        for query in queries[:5]:
            items = await collector.fetch(query=query, limit=30)
            all_items.extend(items)
        # Deduplicate by URL
        seen: set[str] = set()
        deduped = []
        for item in all_items:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                deduped.append(item)
            elif not url:
                deduped.append(item)
        return {"count": len(deduped), "queries": queries, "items": deduped}
    except Exception as exc:
        logger.error("indirect_scrape_failed", error=str(exc))
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
            # Tier 1: Official API channels
            "google":      bool(settings.google_search_api_key and settings.google_search_cx),
            "bing":        bool(settings.scraper_api_key or settings.bing_search_api_key),
            "news":        True,
            "reddit":      bool(settings.reddit_client_id),
            "twitter":     bool(settings.twitter_bearer_token),
            "facebook":    bool(settings.facebook_access_token),
            "instagram":   bool(settings.instagram_access_token),
            "tiktok":      bool(settings.tiktok_client_key),
            "snapchat":    bool(settings.snapchat_access_token),
            "youtube":     bool(settings.youtube_api_key),
            "hackernews":  True,
            # Tier 2: Indirect / no-key channels (always available)
            "indirect": {
                "enabled":           settings.indirect_signals_enabled,
                "nitter_rss":        settings.indirect_nitter_enabled,
                "reddit_public":     settings.indirect_reddit_enabled,
                "tiktok_web":        settings.indirect_tiktok_web_enabled,
                "youtube_rss":       settings.indirect_youtube_rss_enabled,
                "github_trending":   settings.indirect_github_enabled,
                "wikipedia":         settings.indirect_wikipedia_enabled,
                "medium_rss":        settings.indirect_medium_enabled,
                "google_trends":     settings.indirect_google_trends_enabled,
                "instagram_public":  settings.indirect_instagram_public_enabled,
            },
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
