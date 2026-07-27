"""
DATA ENGINE — Indirect Social Signal Collector
Orchestrates all public/API-free trend collection channels.

This is the anti-block layer: when official APIs are rate-limited,
require restricted credentials, or actively block scrapers, this
module collects equivalent trend signals through open channels.

Signal sources (all require zero API keys):
  ─────────────────────────────────────────────────────────────────
  Channel                Output signal
  ─────────────────────────────────────────────────────────────────
  Nitter RSS             Twitter/X trending discussions
  Reddit public JSON     Reddit trending posts (no OAuth)
  TikTok web API         TikTok trending videos (web session)
  YouTube trending RSS   YouTube most-popular per region
  GitHub trending        Developer/tech trending repos
  Wikipedia pageviews    Cultural trending topics
  Medium tag RSS         Long-form tech/trend articles
  Google autocomplete    Real-time Google trending queries
  Google Daily Trends    Today's breakout topics on Google
  Instagram public GQL   Trending hashtag posts (rate-limited)
  ─────────────────────────────────────────────────────────────────

All results are normalised to the same ScrapedItem schema used by
the official social_scraper so the scheduler can merge both pipelines.
"""

from __future__ import annotations

import asyncio
import structlog
from typing import Any

from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator

logger = structlog.get_logger(__name__)


class IndirectSignalCollector:
    """
    Runs all public/open signal sources concurrently and merges results.
    Used as a drop-in complement to SocialScraper — same interface.
    Falls back gracefully when individual channels are blocked or down.
    """

    def __init__(self) -> None:
        self._agg = PublicFeedAggregator()

    async def fetch(self, query: str, limit: int = 50) -> list[dict]:
        """
        Run all indirect channels concurrently.
        Returns up to `limit` normalised trend items.

        Channel failures are silent — a blocked/down channel simply
        contributes 0 results while others continue.
        """
        per_channel = max(10, limit // 6)

        tasks = {
            "google_trends":    self._agg.fetch_google_trends_suggest(query, per_channel),
            "nitter_rss":       self._agg.fetch_twitter_via_nitter(query, per_channel),
            "reddit_public":    self._agg.fetch_reddit_public(query, per_channel),
            "tiktok_web":       self._agg.fetch_tiktok_public(query, per_channel),
            "youtube_rss":      self._agg.fetch_youtube_trending_rss(query, per_channel),
            "github_trending":  self._agg.fetch_github_trending(query, per_channel),
            "wikipedia_views":  self._agg.fetch_wikipedia_trending(query, per_channel),
            "medium_rss":       self._agg.fetch_medium_rss(query, per_channel),
            "instagram_public": self._agg.fetch_instagram_public(query, per_channel),
        }

        outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
        results: list[dict] = []

        for channel, outcome in zip(tasks.keys(), outcomes):
            if isinstance(outcome, Exception):
                logger.debug("indirect_channel_failed", channel=channel, error=str(outcome))
            elif isinstance(outcome, list):
                logger.debug("indirect_channel_ok", channel=channel, count=len(outcome))
                results.extend(outcome)

        # Deduplicate by URL
        seen: set[str] = set()
        deduped: list[dict] = []
        for item in results:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                deduped.append(item)
            elif not url:
                deduped.append(item)

        logger.info(
            "indirect_signals_complete",
            query=query,
            total=len(deduped),
            channels_used=len(tasks),
        )
        return deduped[:limit]

    async def fetch_trending_only(self, limit: int = 30) -> list[dict]:
        """
        Fetch trending content without a specific query — captures
        what's currently trending across the web, not just for a keyword.
        Uses Google Daily Trends + YouTube Trending + Wikipedia as sources.
        """
        from datetime import datetime, UTC
        dummy_query = datetime.now(UTC).strftime("trending %B %Y")
        tasks = [
            self._agg.fetch_google_trends_suggest(dummy_query, limit // 3),
            self._agg.fetch_youtube_trending_rss(dummy_query, limit // 3),
            self._agg.fetch_wikipedia_trending(dummy_query, limit // 3),
            self._agg.fetch_github_trending(dummy_query, limit // 3),
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict] = []
        for o in outcomes:
            if isinstance(o, list):
                results.extend(o)
        return results[:limit]
