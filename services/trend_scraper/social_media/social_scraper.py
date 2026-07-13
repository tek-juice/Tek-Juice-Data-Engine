"""
DATA ENGINE — Social Media Trend Scraper
Collects trending signals from 9 platforms:
  - Hacker News  (public API, no auth required)
  - Reddit       (OAuth2 client credentials)
  - X / Twitter  (Bearer token — Twitter API v2)
  - Facebook     (Graph API access token)
  - Instagram    (Graph API access token — same app as Facebook)
  - TikTok       (TikTok Research API)
  - Snapchat     (Snap Audience Network API)
  - YouTube      (YouTube Data API v3)
  - LinkedIn     (OAuth2 access token)

Each platform gracefully skips if credentials are not configured.
"""

import asyncio
import structlog
import httpx
from datetime import datetime, UTC

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_result(
    platform: str,
    title: str,
    url: str,
    snippet: str = "",
    published_at: str | None = None,
    raw_content: str = "",
    relevance_score: float = 1.0,
    extra_meta: dict | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "snippet": snippet[:500],
        "published_at": published_at or _now_iso(),
        "raw_content": raw_content[:2000],
        "relevance_score": max(0.0, min(1.0, relevance_score)),
        "metadata": {"source": "social_media", "platform": platform, **(extra_meta or {})},
    }


# ─────────────────────────────────────────────────────────────
# Main Scraper
# ─────────────────────────────────────────────────────────────

class SocialScraper:
    """
    Aggregates trend signals from all configured social platforms.
    Platforms with missing credentials are skipped silently.
    """

    SUPPORTED_PLATFORMS = [
        "hackernews",
        "reddit",
        "twitter",
        "facebook",
        "instagram",
        "tiktok",
        "snapchat",
        "youtube",
        "linkedin",
    ]

    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch trending content matching query from all configured platforms.
        Runs all platform fetches concurrently.
        """
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
                results.extend(outcome)

        return results[:limit]

    # ──────────────────────────────────────────
    # Hacker News — public API, no auth needed
    # ──────────────────────────────────────────
    async def _fetch_hackernews(self, query: str, limit: int = 10) -> list[dict]:
        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={"query": query, "tags": "story", "hitsPerPage": limit},
                )
                response.raise_for_status()
                for hit in response.json().get("hits", []):
                    results.append(_make_result(
                        platform="hackernews",
                        title=hit.get("title", ""),
                        url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        snippet=hit.get("story_text", "")[:500] if hit.get("story_text") else "",
                        published_at=hit.get("created_at"),
                        raw_content=hit.get("story_text", ""),
                        relevance_score=min(1.0, (hit.get("points") or 0) / 1000),
                        extra_meta={"points": hit.get("points"), "comments": hit.get("num_comments"), "author": hit.get("author")},
                    ))
        except Exception as exc:
            logger.warning("hackernews_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Reddit — OAuth2 client credentials
    # Docs: https://www.reddit.com/dev/api
    # ──────────────────────────────────────────
    async def _fetch_reddit(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.reddit_client_id or not settings.reddit_client_secret:
            logger.debug("reddit_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Get access token
                token_resp = await client.post(
                    "https://www.reddit.com/api/v1/access_token",
                    auth=(settings.reddit_client_id, settings.reddit_client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"User-Agent": settings.reddit_user_agent},
                )
                token_resp.raise_for_status()
                token = token_resp.json().get("access_token")

                # Search posts
                search_resp = await client.get(
                    "https://oauth.reddit.com/search",
                    params={"q": query, "limit": limit, "sort": "relevance", "t": "week"},
                    headers={"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent},
                )
                search_resp.raise_for_status()

                for post in search_resp.json().get("data", {}).get("children", []):
                    d = post.get("data", {})
                    results.append(_make_result(
                        platform="reddit",
                        title=d.get("title", ""),
                        url=f"https://reddit.com{d.get('permalink', '')}",
                        snippet=d.get("selftext", "")[:500],
                        raw_content=d.get("selftext", ""),
                        relevance_score=min(1.0, (d.get("score") or 0) / 10000),
                        extra_meta={"subreddit": d.get("subreddit"), "score": d.get("score"), "comments": d.get("num_comments")},
                    ))
        except Exception as exc:
            logger.warning("reddit_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # X / Twitter — Bearer token, API v2
    # Docs: https://developer.twitter.com/en/docs/twitter-api
    # ──────────────────────────────────────────
    async def _fetch_twitter(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.twitter_bearer_token:
            logger.debug("twitter_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
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
                response.raise_for_status()
                data = response.json()

                # Build author map
                users = {u["id"]: u["username"] for u in data.get("includes", {}).get("users", [])}

                for tweet in data.get("data", []):
                    metrics = tweet.get("public_metrics", {})
                    author = users.get(tweet.get("author_id", ""), "unknown")
                    score = (metrics.get("like_count", 0) + metrics.get("retweet_count", 0) * 2) / 1000
                    results.append(_make_result(
                        platform="twitter",
                        title=tweet.get("text", "")[:100],
                        url=f"https://twitter.com/{author}/status/{tweet.get('id')}",
                        snippet=tweet.get("text", ""),
                        published_at=tweet.get("created_at"),
                        raw_content=tweet.get("text", ""),
                        relevance_score=min(1.0, score),
                        extra_meta={"author": author, **metrics},
                    ))
        except Exception as exc:
            logger.warning("twitter_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Facebook — Graph API
    # Docs: https://developers.facebook.com/docs/graph-api
    # Requires: pages_read_engagement permission
    # ──────────────────────────────────────────
    async def _fetch_facebook(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.facebook_access_token:
            logger.debug("facebook_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://graph.facebook.com/v20.0/search",
                    params={
                        "q": query,
                        "type": "post",
                        "limit": limit,
                        "fields": "id,message,story,created_time,permalink_url,shares",
                        "access_token": settings.facebook_access_token,
                    },
                )
                response.raise_for_status()

                for post in response.json().get("data", []):
                    text = post.get("message") or post.get("story", "")
                    shares = post.get("shares", {}).get("count", 0)
                    results.append(_make_result(
                        platform="facebook",
                        title=text[:100],
                        url=post.get("permalink_url", f"https://facebook.com/{post.get('id')}"),
                        snippet=text[:500],
                        published_at=post.get("created_time"),
                        raw_content=text,
                        relevance_score=min(1.0, shares / 1000),
                        extra_meta={"post_id": post.get("id"), "shares": shares},
                    ))
        except Exception as exc:
            logger.warning("facebook_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Instagram — Graph API (Business/Creator accounts)
    # Docs: https://developers.facebook.com/docs/instagram-api
    # Requires: instagram_basic, instagram_manage_insights
    # ──────────────────────────────────────────
    async def _fetch_instagram(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.instagram_access_token:
            logger.debug("instagram_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Get connected Instagram Business Account ID first
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
                    logger.debug("instagram_no_business_account")
                    return []

                # Search hashtag
                tag_resp = await client.get(
                    f"https://graph.facebook.com/v20.0/ig_hashtag_search",
                    params={"user_id": ig_id, "q": query.replace(" ", ""), "access_token": settings.instagram_access_token},
                )
                tag_resp.raise_for_status()
                hashtag_ids = [h["id"] for h in tag_resp.json().get("data", [])]

                for hashtag_id in hashtag_ids[:2]:
                    media_resp = await client.get(
                        f"https://graph.facebook.com/v20.0/{hashtag_id}/top_media",
                        params={
                            "user_id": ig_id,
                            "fields": "id,caption,permalink,timestamp,like_count,comments_count",
                            "limit": limit,
                            "access_token": settings.instagram_access_token,
                        },
                    )
                    media_resp.raise_for_status()
                    for media in media_resp.json().get("data", []):
                        caption = media.get("caption", "")
                        likes = media.get("like_count", 0)
                        results.append(_make_result(
                            platform="instagram",
                            title=caption[:100],
                            url=media.get("permalink", f"https://instagram.com/p/{media.get('id')}"),
                            snippet=caption[:500],
                            published_at=media.get("timestamp"),
                            raw_content=caption,
                            relevance_score=min(1.0, likes / 10000),
                            extra_meta={"media_id": media.get("id"), "likes": likes, "comments": media.get("comments_count")},
                        ))
        except Exception as exc:
            logger.warning("instagram_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # TikTok — Research API
    # Docs: https://developers.tiktok.com/products/research-api
    # Requires approved Research API access
    # ──────────────────────────────────────────
    async def _fetch_tiktok(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.tiktok_client_key or not settings.tiktok_client_secret:
            logger.debug("tiktok_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Get access token
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

                # Query videos
                search_resp = await client.post(
                    "https://open.tiktokapis.com/v2/research/video/query/",
                    json={
                        "query": {
                            "and": [{"operation": "IN", "field_name": "keyword", "field_values": [query]}]
                        },
                        "start_date": "20240101",
                        "end_date": datetime.now(UTC).strftime("%Y%m%d"),
                        "max_count": limit,
                        "fields": "id,title,video_description,create_time,like_count,share_count,view_count,author_name",
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                search_resp.raise_for_status()

                for video in search_resp.json().get("data", {}).get("videos", []):
                    views = video.get("view_count", 0)
                    results.append(_make_result(
                        platform="tiktok",
                        title=video.get("title") or video.get("video_description", "")[:100],
                        url=f"https://www.tiktok.com/@{video.get('author_name', 'user')}/video/{video.get('id')}",
                        snippet=video.get("video_description", "")[:500],
                        raw_content=video.get("video_description", ""),
                        relevance_score=min(1.0, views / 1_000_000),
                        extra_meta={
                            "video_id": video.get("id"),
                            "author": video.get("author_name"),
                            "views": views,
                            "likes": video.get("like_count"),
                            "shares": video.get("share_count"),
                        },
                    ))
        except Exception as exc:
            logger.warning("tiktok_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # Snapchat — Snap Audience Network / Public Stories
    # Docs: https://developers.snap.com/api/marketing-api
    # Note: Public trend data is limited; uses marketing insights API
    # ──────────────────────────────────────────
    async def _fetch_snapchat(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.snapchat_access_token:
            logger.debug("snapchat_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Snapchat Discover public stories search
                response = await client.get(
                    "https://storm.snapchat.com/stories/public/",
                    params={"q": query, "limit": limit},
                    headers={
                        "Authorization": f"Bearer {settings.snapchat_access_token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()

                for story in response.json().get("stories", []):
                    results.append(_make_result(
                        platform="snapchat",
                        title=story.get("title", "")[:100],
                        url=story.get("shareUrl", ""),
                        snippet=story.get("description", "")[:500],
                        published_at=story.get("timestamp"),
                        raw_content=story.get("description", ""),
                        relevance_score=1.0,
                        extra_meta={"story_id": story.get("id"), "publisher": story.get("publisher")},
                    ))
        except Exception as exc:
            logger.warning("snapchat_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # YouTube — Data API v3
    # Docs: https://developers.google.com/youtube/v3
    # Enable: YouTube Data API v3 in Google Cloud Console
    # ──────────────────────────────────────────
    async def _fetch_youtube(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.youtube_api_key:
            logger.debug("youtube_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "q": query,
                        "type": "video",
                        "order": "relevance",
                        "maxResults": min(limit, 50),
                        "publishedAfter": "2024-01-01T00:00:00Z",
                        "key": settings.youtube_api_key,
                    },
                )
                response.raise_for_status()

                for item in response.json().get("items", []):
                    snippet = item.get("snippet", {})
                    video_id = item.get("id", {}).get("videoId", "")
                    results.append(_make_result(
                        platform="youtube",
                        title=snippet.get("title", ""),
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        snippet=snippet.get("description", "")[:500],
                        published_at=snippet.get("publishedAt"),
                        raw_content=snippet.get("description", ""),
                        relevance_score=1.0,
                        extra_meta={
                            "video_id": video_id,
                            "channel": snippet.get("channelTitle"),
                            "channel_id": snippet.get("channelId"),
                            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                        },
                    ))
        except Exception as exc:
            logger.warning("youtube_fetch_failed", error=str(exc))
        return results

    # ──────────────────────────────────────────
    # LinkedIn — Marketing API
    # Docs: https://learn.microsoft.com/en-us/linkedin/marketing
    # Requires: r_organization_social or w_member_social scope
    # ──────────────────────────────────────────
    async def _fetch_linkedin(self, query: str, limit: int = 10) -> list[dict]:
        if not settings.linkedin_api_key if hasattr(settings, "linkedin_api_key") else True:
            logger.debug("linkedin_not_configured")
            return []

        results = []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://api.linkedin.com/v2/shares",
                    params={"q": "owners", "owners": "urn:li:organization:company", "count": limit},
                    headers={
                        "Authorization": f"Bearer {getattr(settings, 'linkedin_api_key', '')}",
                        "X-Restli-Protocol-Version": "2.0.0",
                    },
                )
                response.raise_for_status()

                for share in response.json().get("elements", []):
                    content = share.get("specificContent", {}).get("com.linkedin.ugc.ShareContent", {})
                    commentary = content.get("shareCommentary", {}).get("text", "")
                    results.append(_make_result(
                        platform="linkedin",
                        title=commentary[:100],
                        url=share.get("id", ""),
                        snippet=commentary[:500],
                        raw_content=commentary,
                        relevance_score=1.0,
                        extra_meta={"share_id": share.get("id")},
                    ))
        except Exception as exc:
            logger.warning("linkedin_fetch_failed", error=str(exc))
        return results
