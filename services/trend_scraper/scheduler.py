"""
DATA ENGINE — Trend Scraper Scheduler
Orchestrates scraping runs across all configured sources.
Phase 2: Global trend monitoring with API + indirect signal fallback.

Source priority:
  1. Official APIs  (require credentials — highest fidelity)
  2. Indirect/public channels (no credentials — bypass API blocks)
  Both pipelines run concurrently; results are merged and deduped.
"""

import asyncio
import structlog
from datetime import datetime, UTC
from typing import Any

from configs.settings import get_settings
from services.trend_scraper.google.google_scraper import GoogleScraper
from services.trend_scraper.bing.bing_scraper import BingScraper
from services.trend_scraper.news.news_scraper import NewsScraper
from services.trend_scraper.social_media.social_scraper import SocialScraper
from services.trend_scraper.social_media.indirect_signals import IndirectSignalCollector

logger = structlog.get_logger(__name__)
settings = get_settings()


class TrendScraperScheduler:
    """
    Coordinates all scraper sources and persists results.
    Called by the telemetry scheduler every SCRAPER_INTERVAL_SECONDS.

    Sources:
        - google        : Google Custom Search API
        - bing          : Bing Web Search API
        - news          : RSS / news aggregation
        - social_media  : Aggregates 9 platforms concurrently —
                          Hacker News, Reddit, X/Twitter, Facebook,
                          Instagram, TikTok, Snapchat, YouTube, LinkedIn
    """

    def __init__(self) -> None:
        self._scrapers = {
            "google":       GoogleScraper(),
            "bing":         BingScraper(),
            "news":         NewsScraper(),
            "social_media": SocialScraper(),
            # Indirect/public signal channels — no API keys required.
            # These run alongside official APIs and kick in even when
            # official APIs are rate-limited or blocked entirely.
            "indirect":     IndirectSignalCollector(),
        }

    def _free_sources(self) -> list[str]:
        """
        Sources that require zero paid API credits.
        Always-on; used as the default when no specific sources are requested.

        'google' and 'bing' are kept available but excluded from the default
        run to avoid burning paid/free-tier quotas unintentionally:
          - Google CSE: 100 queries/day free — exhausted in seconds at full concurrency
          - ScraperAPI: limited free credits, falls back to direct Bing anyway
          - Bing Microsoft API: requires Azure account
        Both are still reachable by passing sources=["google"] / sources=["bing"]
        or by setting GOOGLE_SEARCH_API_KEY / SCRAPER_API_KEY / BING_SEARCH_API_KEY
        in .env — the scrapers check for keys themselves and skip gracefully.
        """
        free = ["news", "indirect"]
        # Add social_media only if its own indirect channels are not enough
        free.append("social_media")
        # Add Google CSE only when a key is configured (it skips gracefully on 429
        # but we avoid the wasted quota from retries by not including it by default)
        if settings.google_search_api_key and settings.google_search_cx:
            free.append("google")
        # Add Bing only when at least one Bing-specific key is configured
        if settings.scraper_api_key or settings.bing_search_api_key:
            free.append("bing")
        return free

    async def run(
        self,
        sources: list[str] | None = None,
        queries: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Run scraping across specified sources concurrently.

        Args:
            sources: List of source names to scrape. Defaults to free sources only.
                     Pass sources=["google", "bing", ...] to force specific scrapers.
            queries: List of search queries. Defaults to configured defaults.

        Returns:
            Summary dict with counts per source and total elapsed time.
        """
        active_sources = sources or self._free_sources()
        search_queries = queries or self._default_queries()

        start = datetime.now(UTC)
        results: dict[str, int] = {}
        errors: dict[str, str] = {}

        tasks = []
        for source in active_sources:
            scraper = self._scrapers.get(source)
            if scraper:
                tasks.append(
                    self._scrape_source(source, scraper, search_queries)
                )

        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for source, outcome in zip(active_sources, outcomes):
            if isinstance(outcome, Exception):
                errors[source] = str(outcome)
                logger.error("scraper_source_failed", source=source, error=str(outcome))
            else:
                results[source] = outcome
                logger.info("scraper_source_complete", source=source, count=outcome)

        elapsed = (datetime.now(UTC) - start).total_seconds()
        total = sum(results.values())

        logger.info(
            "trend_scraping_complete",
            total=total,
            elapsed_seconds=round(elapsed, 2),
            sources=results,
            errors=errors,
        )
        return {"total": total, "sources": results, "errors": errors, "elapsed_seconds": elapsed}

    async def _scrape_source(
        self, source: str, scraper: Any, queries: list[str]
    ) -> int:
        """Run a single scraper and persist results. Returns count saved."""
        count = 0
        for query in queries:
            try:
                items = await scraper.fetch(query=query, limit=20)
                await self._persist(source=source, query=query, items=items)
                count += len(items)
            except Exception as exc:
                logger.warning("scraper_query_failed", source=source, query=query, error=str(exc))
        return count

    async def _persist(self, source: str, query: str, items: list[dict]) -> None:
        """Persist scraped items to the scraped_trends table."""
        if not items:
            return
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from datetime import datetime
        import json

        async with AsyncSessionLocal() as session:
            for item in items:
                # asyncpg requires datetime objects for TIMESTAMPTZ — parse ISO strings
                published_at_raw = item.get("published_at")
                if isinstance(published_at_raw, str):
                    try:
                        published_at = datetime.fromisoformat(
                            published_at_raw.replace("Z", "+00:00")
                        )
                    except ValueError:
                        published_at = None
                else:
                    published_at = published_at_raw

                await session.execute(
                    text("""
                        INSERT INTO scraped_trends
                            (source, query, title, url, snippet, published_at,
                             raw_content, relevance_score, metadata)
                        VALUES
                            (:source, :query, :title, :url, :snippet, :published_at,
                             :raw_content, :relevance_score, :metadata)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "source":          source,
                        "query":           query,
                        "title":           item.get("title"),
                        "url":             item.get("url"),
                        "snippet":         item.get("snippet"),
                        "published_at":    published_at,
                        "raw_content":     item.get("raw_content"),
                        "relevance_score": item.get("relevance_score", 1.0),
                        "metadata":        json.dumps(item.get("metadata", {})),
                    },
                )
            await session.commit()

    @staticmethod
    def _default_queries() -> list[str]:
        return [
            "AI data processing trends 2025",
            "vector database technology",
            "semantic search innovations",
            "large language model optimization",
            "enterprise AI infrastructure",
        ]

    @staticmethod
    def available_platforms() -> dict[str, list[str]]:
        """Return a map of sources to their sub-platforms for observability."""
        return {
            "google":       ["google_custom_search"],
            "bing":         ["bing_web_search"],
            "news":         ["rss_aggregator"],
            "social_media": [
                "hackernews",
                "reddit",
                "twitter",
                "facebook",
                "instagram",
                "tiktok",
                "snapchat",
                "youtube",
                "linkedin",
            ],
            # Indirect channels — no API keys required
            "indirect": [
                "google_trends_autocomplete",
                "google_daily_trends",
                "nitter_rss",           # Twitter/X via public mirror
                "reddit_public_json",   # Reddit without OAuth
                "tiktok_web_api",       # TikTok web session (no app auth)
                "youtube_trending_rss", # YouTube trending without API key
                "github_trending",      # Developer/tech signals
                "wikipedia_pageviews",  # Cultural trending topics
                "medium_rss",           # Long-form trend articles
                "instagram_public_gql", # Instagram hashtag (rate-limited)
            ],
        }
