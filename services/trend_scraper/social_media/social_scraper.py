"""
DATA ENGINE — Social Media Trend Scraper
Collects trending signals from 9 platforms with full Pydantic validation.

Every platform response is validated against a strict schema model.
If a platform modifies its API payload shape:
  - ValidationError is caught gracefully
  - Raw payload is saved to dead-letter queue
  - Dashboard alert fires after ALERT_THRESHOLD consecutive failures
  - Worker pipeline continues without crashing

Strategy — Free-first, paid-as-bonus:
  Each platform tries its free/public channel first (feed_aggregator).
  If a paid API key is configured it runs in parallel and results are merged.
  This means zero API keys = full coverage, paid keys = bonus signal volume.

Platforms:
  - Hacker News  (public API, no auth — always free)
  - Reddit       (public JSON first; OAuth2 bonus if key present)
  - X / Twitter  (Nitter RSS first; Bearer token bonus if key present)
  - Facebook     (RSS page feeds first; Graph API bonus if token present)
  - Instagram    (public GraphQL first; Graph API bonus if token present)
  - TikTok       (web session first; Research API bonus if key present)
  - Snapchat     (Spotlight public discovery first; API bonus if token present)
  - YouTube      (trending RSS first; Data API v3 bonus if key present)
  - LinkedIn     (public page scrape first; API bonus if key present)
"""

from __future__ import annotations

import asyncio
import structlog
import httpx
from datetime import datetime, UTC
from pydantic import ValidationError

from configs.settings import get_settings
from services.trend_scraper.validators.scraper_models import (
    HackerNewsResponse,
    RedditResponse,
    TwitterResponse,
    FacebookResponse,
    InstagramMediaResponse,
    InstagramHashtagResponse,
    TikTokResponse,
    SnapchatResponse,
    YouTubeResponse,
    LinkedInResponse,
    ScrapedItem,
)
from services.trend_scraper.validators.dead_letter import (
    send_to_dead_letter,
    record_scraper_success,
)

logger = structlog.get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_item(
    platform: str,
    title: str,
    url: str,
    snippet: str = "",
    published_at: str | None = None,
    raw_content: str = "",
    relevance_score: float = 1.0,
    extra_meta: dict | None = None,
) -> ScrapedItem:
    return ScrapedItem(
        platform=platform,
        title=title,
        url=url,
        snippet=snippet,
        published_at=published_at or _now_iso(),
        raw_content=raw_content,
        relevance_score=relevance_score,
        metadata={"source": "social_media", "platform": platform, **(extra_meta or {})},
    )


# ─────────────────────────────────────────────
# Main Scraper
# ─────────────────────────────────────────────

class SocialScraper:
    """
    Aggregates trend signals from all configured social platforms.
    All platforms run concurrently. Missing credentials skip silently.
    API schema changes are caught, dead-lettered, and alerted.
    """

    SUPPORTED_PLATFORMS = [
        "hackernews", "reddit", "twitter", "facebook",
        "instagram", "tiktok", "snapchat", "youtube", "linkedin",
    ]

    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        tasks = [
            self._fetch_hackernews(query, limit),
            self._fetch_reddit(query, limit),
            self._fetch_twitter(query, limit),
            self._fetch_facebook(query, limit),
            self._fetch_instagram(query, limit),
            self._fetch_tiktok(query, limit),
            self._fetch_snapchat(query, limit),
            self._fetch_youtube(query, limit),
            self._fetch_linkedin(query, limit),
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict] = []
        for platform, outcome in zip(self.SUPPORTED_PLATFORMS, outcomes):
            if isinstance(outcome, Exception):
                logger.warning("social_platform_failed", platform=platform, error=str(outcome))
            elif isinstance(outcome, list):
                results.extend([item.model_dump() for item in outcome])
        return results[:limit]

    # ──────────────────────────────────────────
    # Hacker News
    # ──────────────────────────────────────────
    async def _fetch_hackernews(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={"query": query, "tags": "story", "hitsPerPage": limit},
                )
                resp.raise_for_status()
                raw = resp.json()

            try:
                validated = HackerNewsResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="hackernews", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError",
                    error_detail=str(exc),
                )
                return []

            for hit in validated.hits:
                results.append(_make_item(
                    platform="hackernews",
                    title=hit.title or "",
                    url=hit.url or f"https://news.ycombinator.com/item?id={hit.objectID}",
                    snippet=(hit.story_text or "")[:500],
                    published_at=hit.created_at,
                    raw_content=hit.story_text or "",
                    relevance_score=min(1.0, (hit.points or 0) / 1000),
                    extra_meta={"points": hit.points, "comments": hit.num_comments, "author": hit.author},
                ))
            await record_scraper_success("hackernews")

        except Exception as exc:
            await send_to_dead_letter(
                platform="hackernews", source="social_media", query=query,
                raw_payload={}, error_type=type(exc).__name__, error_detail=str(exc),
            )
        return results

    # ──────────────────────────────────────────
    # Reddit — public JSON first, OAuth bonus
    # ──────────────────────────────────────────
    async def _fetch_reddit(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        # Always run free public JSON path
        free_task  = agg.fetch_reddit_public(query, limit)
        # Run OAuth only when credentials are present
        paid_task  = self._fetch_reddit_oauth(query, limit) if (
            settings.reddit_client_id and settings.reddit_client_secret
        ) else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="reddit",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("reddit")
        return results[:limit]

    async def _fetch_reddit_oauth(self, query: str, limit: int) -> list[ScrapedItem]:
        """OAuth path — bonus results on top of public JSON."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                token_resp = await client.post(
                    "https://www.reddit.com/api/v1/access_token",
                    auth=(settings.reddit_client_id, settings.reddit_client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"User-Agent": settings.reddit_user_agent},
                )
                token_resp.raise_for_status()
                token = token_resp.json().get("access_token")
                search_resp = await client.get(
                    "https://oauth.reddit.com/search",
                    params={"q": query, "limit": limit, "sort": "relevance", "t": "week"},
                    headers={"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent},
                )
                search_resp.raise_for_status()
                raw = search_resp.json()
            try:
                validated = RedditResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="reddit", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []
            for child in validated.data.children:
                d = child.data
                results.append(_make_item(
                    platform="reddit",
                    title=d.title or "",
                    url=f"https://reddit.com{d.permalink or ''}",
                    snippet=(d.selftext or "")[:500],
                    raw_content=d.selftext or "",
                    relevance_score=min(1.0, (d.score or 0) / 10000),
                    extra_meta={"subreddit": d.subreddit, "score": d.score, "comments": d.num_comments},
                ))
        except Exception as exc:
            logger.debug("reddit_oauth_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # X / Twitter — Nitter RSS first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_twitter(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_twitter_via_nitter(query, limit)
        paid_task = self._fetch_twitter_api(query, limit) if settings.twitter_bearer_token else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="twitter",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("twitter")
        return results[:limit]

    async def _fetch_twitter_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Bearer token path — bonus results on top of Nitter."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    params={
                        "query": f"{query} -is:retweet lang:en",
                        "max_results": min(limit, 100),
                        "tweet.fields": "created_at,public_metrics,author_id",
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                    headers={"Authorization": f"Bearer {settings.twitter_bearer_token}"},
                )
                resp.raise_for_status()
                raw = resp.json()
            try:
                validated = TwitterResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="twitter", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []
            users = {u.id: u.username for u in validated.includes.users}
            for tweet in validated.data:
                m = tweet.public_metrics
                author = users.get(tweet.author_id or "", "unknown")
                score = (m.like_count + m.retweet_count * 2) / 1000
                results.append(_make_item(
                    platform="twitter",
                    title=(tweet.text or "")[:100],
                    url=f"https://twitter.com/{author}/status/{tweet.id}",
                    snippet=tweet.text or "",
                    published_at=tweet.created_at,
                    raw_content=tweet.text or "",
                    relevance_score=min(1.0, score),
                    extra_meta={"author": author, "likes": m.like_count, "retweets": m.retweet_count},
                ))
        except Exception as exc:
            logger.debug("twitter_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Facebook — RSS page feeds first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_facebook(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_facebook_public(query, limit)
        paid_task = self._fetch_facebook_api(query, limit) if settings.facebook_access_token else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="facebook",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("facebook")
        return results[:limit]

    async def _fetch_facebook_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Graph API path — bonus when token is present."""
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://graph.facebook.com/v20.0/search",
                    params={
                        "q": query, "type": "post", "limit": limit,
                        "fields": "id,message,story,created_time,permalink_url,shares",
                        "access_token": settings.facebook_access_token,
                    },
                )
                resp.raise_for_status()
                raw = resp.json()
            try:
                validated = FacebookResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="facebook", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []
            for post in validated.data:
                text = post.message or post.story or ""
                results.append(_make_item(
                    platform="facebook",
                    title=text[:100],
                    url=post.permalink_url or f"https://facebook.com/{post.id}",
                    snippet=text[:500],
                    published_at=post.created_time,
                    raw_content=text,
                    relevance_score=min(1.0, post.shares.count / 1000),
                    extra_meta={"post_id": post.id, "shares": post.shares.count},
                ))
        except Exception as exc:
            logger.debug("facebook_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Instagram — public GQL first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_instagram(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_instagram_public(query, limit)
        paid_task = self._fetch_instagram_api(query, limit) if settings.instagram_access_token else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="instagram",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("instagram")
        return results[:limit]

    async def _fetch_instagram_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Graph API path — bonus when token is present."""
        if not settings.instagram_access_token:
            return []
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                me_resp = await client.get(
                    "https://graph.facebook.com/v20.0/me/accounts",
                    params={"access_token": settings.instagram_access_token, "fields": "instagram_business_account"},
                )
                me_resp.raise_for_status()
                pages = me_resp.json().get("data", [])
                ig_id = next(
                    (p["instagram_business_account"]["id"] for p in pages if p.get("instagram_business_account")),
                    None,
                )
                if not ig_id:
                    return []

                tag_resp = await client.get(
                    "https://graph.facebook.com/v20.0/ig_hashtag_search",
                    params={"user_id": ig_id, "q": query.replace(" ", ""),
                            "access_token": settings.instagram_access_token},
                )
                tag_resp.raise_for_status()
                raw_tags = tag_resp.json()

                try:
                    validated_tags = InstagramHashtagResponse.model_validate(raw_tags)
                except ValidationError as exc:
                    await send_to_dead_letter(
                        platform="instagram", source="social_media", query=query,
                        raw_payload=raw_tags, error_type="ValidationError", error_detail=str(exc),
                    )
                    return []

                for hashtag in validated_tags.data[:2]:
                    if not hashtag.id:
                        continue
                    media_resp = await client.get(
                        f"https://graph.facebook.com/v20.0/{hashtag.id}/top_media",
                        params={
                            "user_id": ig_id,
                            "fields": "id,caption,permalink,timestamp,like_count,comments_count",
                            "limit": limit,
                            "access_token": settings.instagram_access_token,
                        },
                    )
                    media_resp.raise_for_status()
                    raw_media = media_resp.json()

                    try:
                        validated_media = InstagramMediaResponse.model_validate(raw_media)
                    except ValidationError as exc:
                        await send_to_dead_letter(
                            platform="instagram", source="social_media", query=query,
                            raw_payload=raw_media, error_type="ValidationError", error_detail=str(exc),
                        )
                        continue

                    for media in validated_media.data:
                        caption = media.caption or ""
                        results.append(_make_item(
                            platform="instagram",
                            title=caption[:100],
                            url=media.permalink or f"https://instagram.com/p/{media.id}",
                            snippet=caption[:500],
                            published_at=media.timestamp,
                            raw_content=caption,
                            relevance_score=min(1.0, (media.like_count or 0) / 10000),
                            extra_meta={"media_id": media.id, "likes": media.like_count,
                                        "comments": media.comments_count},
                        ))
        except Exception as exc:
            logger.debug("instagram_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # TikTok — web session first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_tiktok(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_tiktok_public(query, limit)
        paid_task = self._fetch_tiktok_api(query, limit) if (
            settings.tiktok_client_key and settings.tiktok_client_secret
        ) else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="tiktok",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("tiktok")
        return results[:limit]

    async def _fetch_tiktok_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Research API path — bonus when credentials present."""
        if not settings.tiktok_client_key or not settings.tiktok_client_secret:
            return []
        results = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    "https://open.tiktokapis.com/v2/oauth/token/",
                    data={
                        "client_key": settings.tiktok_client_key,
                        "client_secret": settings.tiktok_client_secret,
                        "grant_type": "client_credentials",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                token_resp.raise_for_status()
                token = token_resp.json().get("access_token")

                search_resp = await client.post(
                    "https://open.tiktokapis.com/v2/research/video/query/",
                    json={
                        "query": {"and": [{"operation": "IN", "field_name": "keyword", "field_values": [query]}]},
                        "start_date": "20240101",
                        "end_date": datetime.now(UTC).strftime("%Y%m%d"),
                        "max_count": limit,
                        "fields": "id,title,video_description,create_time,like_count,share_count,view_count,author_name",
                    },
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
                search_resp.raise_for_status()
                raw = search_resp.json()

            try:
                validated = TikTokResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="tiktok", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []

            for video in validated.data.videos:
                views = video.view_count or 0
                results.append(_make_item(
                    platform="tiktok",
                    title=video.title or (video.video_description or "")[:100],
                    url=f"https://www.tiktok.com/@{video.author_name or 'user'}/video/{video.id}",
                    snippet=(video.video_description or "")[:500],
                    raw_content=video.video_description or "",
                    relevance_score=min(1.0, views / 1_000_000),
                    extra_meta={"video_id": video.id, "author": video.author_name,
                                "views": views, "likes": video.like_count, "shares": video.share_count},
                ))
        except Exception as exc:
            logger.debug("tiktok_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Snapchat — Spotlight public first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_snapchat(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_snapchat_public(query, limit)
        paid_task = self._fetch_snapchat_api(query, limit) if settings.snapchat_access_token else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="snapchat",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("snapchat")
        return results[:limit]

    async def _fetch_snapchat_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Audience Network API path — bonus when token present."""
        if not settings.snapchat_access_token:
            return []
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://storm.snapchat.com/stories/public/",
                    params={"q": query, "limit": limit},
                    headers={"Authorization": f"Bearer {settings.snapchat_access_token}",
                             "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                raw = resp.json()

            try:
                validated = SnapchatResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="snapchat", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []

            for story in validated.stories:
                results.append(_make_item(
                    platform="snapchat",
                    title=(story.title or "")[:100],
                    url=story.shareUrl or "",
                    snippet=(story.description or "")[:500],
                    published_at=story.timestamp,
                    raw_content=story.description or "",
                    extra_meta={"story_id": story.id, "publisher": story.publisher},
                ))
        except Exception as exc:
            logger.debug("snapchat_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # YouTube — RSS trending first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_youtube(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_youtube_trending_rss(query, limit)
        paid_task = self._fetch_youtube_api(query, limit) if settings.youtube_api_key else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="youtube",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("youtube")
        return results[:limit]

    async def _fetch_youtube_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """Data API v3 path — bonus keyword search when key present."""
        if not settings.youtube_api_key:
            return []
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet", "q": query, "type": "video",
                        "order": "relevance", "maxResults": min(limit, 50),
                        "publishedAfter": "2024-01-01T00:00:00Z",
                        "key": settings.youtube_api_key,
                    },
                )
                resp.raise_for_status()
                raw = resp.json()

            try:
                validated = YouTubeResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="youtube", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []

            for item in validated.items:
                s = item.snippet
                video_id = item.id.videoId or ""
                results.append(_make_item(
                    platform="youtube",
                    title=s.title or "",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    snippet=(s.description or "")[:500],
                    published_at=s.publishedAt,
                    raw_content=s.description or "",
                    extra_meta={"video_id": video_id, "channel": s.channelTitle,
                                "channel_id": s.channelId,
                                "thumbnail": s.thumbnails.default.url},
                ))
            await record_scraper_success("youtube")

        except Exception as exc:
            logger.debug("youtube_api_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # LinkedIn — public scrape first, API bonus
    # ──────────────────────────────────────────
    async def _fetch_linkedin(self, query: str, limit: int = 10) -> list[ScrapedItem]:
        from services.trend_scraper.utils.feed_aggregator import PublicFeedAggregator
        agg = PublicFeedAggregator()

        free_task = agg.fetch_linkedin_public(query, limit)
        paid_task = self._fetch_linkedin_api(query, limit) if getattr(settings, "linkedin_api_key", "") else asyncio.coroutine(lambda: [])()

        free_items, paid_items = await asyncio.gather(free_task, paid_task, return_exceptions=True)

        results: list[ScrapedItem] = []
        seen_urls: set[str] = set()

        for item in (paid_items if isinstance(paid_items, list) else []):
            url = item.url
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(item)

        for raw in (free_items if isinstance(free_items, list) else []):
            url = raw.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(_make_item(
                    platform="linkedin",
                    title=raw.get("title", ""),
                    url=url,
                    snippet=raw.get("snippet", ""),
                    raw_content=raw.get("raw_content", ""),
                    relevance_score=raw.get("relevance_score", 1.0),
                    extra_meta=raw.get("metadata", {}),
                ))

        await record_scraper_success("linkedin")
        return results[:limit]

    async def _fetch_linkedin_api(self, query: str, limit: int) -> list[ScrapedItem]:
        """LinkedIn API path — bonus when key present."""
        if not getattr(settings, "linkedin_api_key", ""):
            return []
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://api.linkedin.com/v2/shares",
                    params={"q": "owners", "owners": "urn:li:organization:company", "count": limit},
                    headers={"Authorization": f"Bearer {getattr(settings, 'linkedin_api_key', '')}",
                             "X-Restli-Protocol-Version": "2.0.0"},
                )
                resp.raise_for_status()
                raw = resp.json()

            try:
                validated = LinkedInResponse.model_validate(raw)
            except ValidationError as exc:
                await send_to_dead_letter(
                    platform="linkedin", source="social_media", query=query,
                    raw_payload=raw, error_type="ValidationError", error_detail=str(exc),
                )
                return []

            for share in validated.elements:
                content = share.specificContent.share_content
                commentary = content.shareCommentary.text or ""
                results.append(_make_item(
                    platform="linkedin",
                    title=commentary[:100],
                    url=share.id or "",
                    snippet=commentary[:500],
                    raw_content=commentary,
                    extra_meta={"share_id": share.id},
                ))
        except Exception as exc:
            logger.debug("linkedin_api_failed", error=str(exc))
        return results
