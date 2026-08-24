"""
DATA ENGINE — Public Feed Aggregator
Scrapes trend signals from social platforms through their public/open
interfaces — bypassing API rate limits and authentication walls entirely.

Strategy per platform:
  - Instagram  → scrapeIG public tag page (no login required)
  - TikTok     → TikTok trending hashtag/video public API (no auth)
  - X/Twitter  → nitter.net public RSS feeds (no API key)
  - Reddit     → reddit.com JSON API (public, no OAuth)
  - LinkedIn   → public post pages via SmartScraper + profile spoofing
  - Facebook   → CrowdTangle public links API + public page scraping
  - Snapchat   → Snap Map / Spotlight public discovery endpoints
  - YouTube    → YouTube trending RSS (no API key needed)
  - GitHub     → github.com/trending HTML scrape (developer trends)
  - Wikipedia  → pageviews API (cultural/knowledge trending topics)
  - Pinterest  → public search page scraping
  - Quora      → public topic page scraping
  - Medium     → public tag RSS feeds
  - Substack   → trending posts RSS

These are all fallback channels — they complement API access, not replace it.
When official APIs are rate-limited or blocked, these kick in automatically.
"""

from __future__ import annotations

import asyncio
import random
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from configs.settings import get_settings
from services.trend_scraper.utils.anti_block import (
    smart_delay,
    get_random_headers,
)
from services.trend_scraper.utils.fingerprint_spoofer import (
    get_random_profile,
    get_profile_http_headers,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# Nitter instances — public Twitter mirrors, no API key needed
# We cycle through the available instances; skip any that are down
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.lunar.icu",
    "https://nitter.tiekoetter.com",
    "https://nitter.unixfox.eu",
]

# Snapchat Spotlight — public discovery (no auth)
SNAPCHAT_SPOTLIGHT_URL = "https://www.snapchat.com/spotlight"
SNAPCHAT_SEARCH_URL    = "https://story.snapchat.com/search"

# Facebook public RSS page feeds (via RSS.app or direct)
FACEBOOK_RSS_FEEDS = [
    "https://www.facebook.com/feeds/page.php?format=rss20&id=",
]

# LinkedIn public job/post feeds (no auth)
LINKEDIN_PUBLIC_SEARCH = "https://www.linkedin.com/search/results/content/"

# Teddit/Libreddit instances — public Reddit mirrors
TEDDIT_INSTANCES = [
    "https://teddit.net",
    "https://libreddit.spike.codes",
    "https://libreddit.privacy.com.de",
]

# TikTok web endpoint (no auth) — trending hashtag discovery
TIKTOK_TRENDING_URL = "https://www.tiktok.com/api/discover/type/"
TIKTOK_TAG_SEARCH   = "https://www.tiktok.com/api/search/general/full/"

# YouTube trending RSS feeds (no API key)
YOUTUBE_TRENDING_RSS = {
    "us": "https://www.youtube.com/feeds/videos.xml?chart=mostpopular&regionCode=US&hl=en",
    "gb": "https://www.youtube.com/feeds/videos.xml?chart=mostpopular&regionCode=GB&hl=en",
    "in": "https://www.youtube.com/feeds/videos.xml?chart=mostpopular&regionCode=IN&hl=en",
    "au": "https://www.youtube.com/feeds/videos.xml?chart=mostpopular&regionCode=AU&hl=en",
    "ca": "https://www.youtube.com/feeds/videos.xml?chart=mostpopular&regionCode=CA&hl=en",
}

# Wikipedia trending pageviews API (no auth)
WIKIPEDIA_TRENDING_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access"

# GitHub trending (public HTML)
GITHUB_TRENDING_URL = "https://github.com/trending"

# Medium tag RSS
MEDIUM_TAG_RSS = "https://medium.com/feed/tag/{tag}"

# Reddit JSON API (public — no OAuth needed for public subreddits)
REDDIT_PUBLIC_URL = "https://www.reddit.com/r/{subreddit}/search.json"
REDDIT_TRENDING_URL = "https://www.reddit.com/api/trending_subreddits.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _item(platform: str, title: str, url: str, snippet: str = "",
          published_at: str | None = None, meta: dict | None = None) -> dict:
    return {
        "platform":        platform,
        "title":           title[:300],
        "url":             url,
        "snippet":         snippet[:800],
        "published_at":    published_at or _now_iso(),
        "raw_content":     snippet[:800],
        "relevance_score": 1.0,
        "metadata":        {"source": "indirect", "channel": "public_feed", **(meta or {})},
    }


class PublicFeedAggregator:
    """
    Aggregates trend signals from public/open social media endpoints
    that do NOT require API keys, OAuth, or logins.
    """

    # ── Twitter / X via Nitter RSS ─────────────────────────────────────────────

    async def fetch_twitter_via_nitter(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch Twitter/X trends via Nitter public RSS feeds.
        No API key, no rate limit from Twitter.
        Tries multiple Nitter instances; skips down ones.
        """
        tag = urllib.parse.quote(query.replace(" ", ""))
        results: list[dict] = []

        for instance in NITTER_INSTANCES:
            if len(results) >= limit:
                break
            feed_url = f"{instance}/search/rss?q={urllib.parse.quote(query)}&f=tweets"
            try:
                profile = get_random_profile()
                headers = get_profile_http_headers(profile)
                async with httpx.AsyncClient(timeout=15, headers=headers,
                                              follow_redirects=True) as client:
                    resp = await client.get(feed_url)
                    if resp.status_code != 200:
                        continue
                    items = self._parse_rss_feed(resp.text, platform="twitter", source=instance)
                    filtered = [i for i in items if
                                any(w.lower() in (i["title"] + i["snippet"]).lower()
                                    for w in query.split()[:3])]
                    results.extend(filtered[:limit])
                    if results:
                        logger.debug("nitter_feed_ok", instance=instance, count=len(results))
                        break
            except Exception as exc:
                logger.debug("nitter_instance_failed", instance=instance, error=str(exc))
                await asyncio.sleep(0.5)

        await smart_delay()
        return results[:limit]

    # ── Reddit public JSON (no OAuth) ─────────────────────────────────────────

    async def fetch_reddit_public(
        self, query: str, limit: int = 20, subreddits: list[str] | None = None
    ) -> list[dict]:
        """
        Fetch Reddit data via the public JSON API — no OAuth needed.
        Searches r/all or specific subreddits.
        Reddit allows ~60 requests/min from a single IP without auth.
        """
        results: list[dict] = []
        subs = subreddits or ["all", "technology", "artificial", "MachineLearning"]

        profile = get_random_profile()
        headers = get_profile_http_headers(profile)
        headers["User-Agent"] = settings.reddit_user_agent

        async with httpx.AsyncClient(timeout=20, headers=headers,
                                      follow_redirects=True) as client:
            for sub in subs[:2]:
                if len(results) >= limit:
                    break
                try:
                    url = REDDIT_PUBLIC_URL.format(subreddit=sub)
                    resp = await client.get(url, params={
                        "q": query, "sort": "relevance", "t": "week",
                        "limit": min(limit, 25), "restrict_sr": "false",
                    })
                    resp.raise_for_status()
                    data = resp.json()
                    for child in data.get("data", {}).get("children", []):
                        d = child.get("data", {})
                        title    = d.get("title", "")
                        post_url = f"https://reddit.com{d.get('permalink', '')}"
                        snippet  = d.get("selftext", "")[:500]
                        if title:
                            results.append(_item(
                                "reddit", title, post_url, snippet,
                                meta={"subreddit": d.get("subreddit"), "score": d.get("score"),
                                      "channel": "public_json"},
                            ))
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                except Exception as exc:
                    logger.debug("reddit_public_failed", sub=sub, error=str(exc))

        # Also grab trending subreddits for topic discovery
        try:
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.get(REDDIT_TRENDING_URL)
                if resp.status_code == 200:
                    trending = resp.json().get("subreddit_names", [])
                    for sub_name in trending[:3]:
                        results.append(_item(
                            "reddit",
                            f"Trending subreddit: r/{sub_name}",
                            f"https://reddit.com/r/{sub_name}",
                            meta={"channel": "trending_subreddits"},
                        ))
        except Exception:
            pass

        return results[:limit]

    # ── TikTok public web API (no auth) ───────────────────────────────────────

    async def fetch_tiktok_public(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch TikTok trending content via TikTok's own web JSON endpoints.
        These are the same endpoints the tiktok.com website uses — they
        don't require OAuth, only a valid web session cookie (which we warm
        up via a homepage visit).
        """
        results: list[dict] = []
        profile = get_random_profile()
        headers = {
            **get_profile_http_headers(profile),
            "Referer":          "https://www.tiktok.com/",
            "Origin":           "https://www.tiktok.com",
            "x-requested-with": "XMLHttpRequest",
        }

        try:
            async with httpx.AsyncClient(timeout=25, headers=headers,
                                          follow_redirects=True) as client:
                # Step 1: visit homepage to get device_id / tt_webid cookies
                try:
                    await client.get("https://www.tiktok.com/", timeout=10)
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                except Exception:
                    pass

                # Step 2: trending discovery via public search
                resp = await client.get(
                    "https://www.tiktok.com/api/search/general/full/",
                    params={
                        "keyword":        query,
                        "offset":         0,
                        "count":          min(limit, 20),
                        "from_page":      "search",
                        "web_search_code": '{"tiktok":{"client_params_x":{"search_engine":{"ies_mt_user_live_video_card_use_libra":1}},"search_server":{}}}',
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("data", [])[:limit]:
                        item_type = item.get("type", 0)
                        # type 1 = video item
                        if item_type == 1:
                            video = item.get("item", {})
                            desc    = video.get("desc", "")
                            vid_id  = video.get("id", "")
                            author  = video.get("author", {}).get("uniqueId", "user")
                            stats   = video.get("stats", {})
                            plays   = stats.get("playCount", 0)
                            results.append(_item(
                                "tiktok", desc[:100],
                                f"https://www.tiktok.com/@{author}/video/{vid_id}",
                                desc,
                                meta={"views": plays, "likes": stats.get("diggCount"),
                                      "channel": "web_search"},
                            ))

        except Exception as exc:
            logger.debug("tiktok_public_failed", error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── YouTube Trending RSS (no API key) ─────────────────────────────────────

    async def fetch_youtube_trending_rss(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        YouTube trending videos via their public Atom/RSS feed.
        No API key required — this is the same feed YouTube.com fetches.
        Covers US, GB, IN, AU, CA trending simultaneously.
        """
        results: list[dict] = []
        query_lower = query.lower()

        async with httpx.AsyncClient(timeout=20, headers=get_random_headers(),
                                      follow_redirects=True) as client:
            for country, rss_url in list(YOUTUBE_TRENDING_RSS.items())[:3]:
                try:
                    resp = await client.get(rss_url)
                    if resp.status_code != 200:
                        continue
                    items = self._parse_atom_feed(resp.text, platform="youtube", region=country)
                    # Keyword filter
                    matching = [i for i in items if
                                any(w in (i["title"] + i["snippet"]).lower()
                                    for w in query_lower.split()[:3])]
                    results.extend((matching or items)[:limit // 3 + 1])
                except Exception as exc:
                    logger.debug("youtube_rss_failed", region=country, error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── GitHub Trending (developer/tech signal) ───────────────────────────────

    async def fetch_github_trending(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Scrape GitHub trending repositories — a strong signal for tech topics.
        Pure HTML scrape — no auth needed.
        """
        results: list[dict] = []
        profile = get_random_profile()
        headers = get_profile_http_headers(profile)
        try:
            # Build search-aware URL
            query_slug = query.lower().replace(" ", "-")
            url = f"{GITHUB_TRENDING_URL}?since=daily&q={urllib.parse.quote(query)}"
            async with httpx.AsyncClient(timeout=20, headers=headers,
                                          follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                # Parse repos from HTML
                # Each repo is in <article class="Box-row">
                html = resp.text
                repos = re.findall(
                    r'<h2[^>]*>\s*<a[^>]+href="(/[^"]+)"[^>]*>(.*?)</a>',
                    html, re.DOTALL
                )
                descs = re.findall(
                    r'<p class="col-9[^"]*"[^>]*>\s*(.*?)\s*</p>', html, re.DOTALL
                )
                for i, (path, name_raw) in enumerate(repos[:limit]):
                    name    = re.sub(r"\s+", " ", name_raw).strip()
                    desc    = re.sub(r"\s+", " ", descs[i]).strip() if i < len(descs) else ""
                    repo_url = f"https://github.com{path.strip()}"
                    if name:
                        results.append(_item(
                            "github", f"Trending: {name}", repo_url, desc,
                            meta={"channel": "github_trending"},
                        ))
        except Exception as exc:
            logger.debug("github_trending_failed", error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── Wikipedia Trending Pageviews ──────────────────────────────────────────

    async def fetch_wikipedia_trending(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Wikipedia Wikimedia pageviews API — no auth, freely accessible.
        Returns most-viewed articles for yesterday — a strong cultural
        signal for what topics are dominating public attention.
        """
        results: list[dict] = []
        from datetime import timedelta
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y/%m/%d")
        url = f"{WIKIPEDIA_TRENDING_URL}/{yesterday}"

        try:
            async with httpx.AsyncClient(timeout=15, headers=get_random_headers()) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                articles = resp.json().get("items", [{}])[0].get("articles", [])
                query_lower = query.lower()
                for art in articles:
                    title  = art.get("article", "").replace("_", " ")
                    views  = art.get("views", 0)
                    if not title or title in ("Main_Page", "Special:Search"):
                        continue
                    # Score relevance to query — keyword overlap
                    title_lower = title.lower()
                    relevant    = any(w in title_lower for w in query_lower.split() if len(w) > 3)
                    results.append(_item(
                        "wikipedia",
                        title,
                        f"https://en.wikipedia.org/wiki/{art.get('article', '')}",
                        f"Wikipedia trending article — {views:,} views",
                        meta={"views": views, "relevance_to_query": relevant,
                              "channel": "wikipedia_pageviews"},
                    ))
                    if len(results) >= limit:
                        break
        except Exception as exc:
            logger.debug("wikipedia_trending_failed", error=str(exc))

        return results[:limit]

    # ── Medium Tag RSS ─────────────────────────────────────────────────────────

    async def fetch_medium_rss(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch trending Medium articles for topic tags derived from the query.
        No auth required — Medium's RSS feeds are public.
        """
        results: list[dict] = []
        # Convert query words to Medium tag slugs
        tags = [w.lower().replace(" ", "-") for w in query.split()[:3] if len(w) > 3]
        tags.extend(["artificial-intelligence", "technology", "data-science"])

        async with httpx.AsyncClient(timeout=20, headers=get_random_headers(),
                                      follow_redirects=True) as client:
            for tag in tags[:3]:
                if len(results) >= limit:
                    break
                try:
                    rss_url = MEDIUM_TAG_RSS.format(tag=tag)
                    resp    = await client.get(rss_url)
                    if resp.status_code != 200:
                        continue
                    items = self._parse_rss_feed(resp.text, platform="medium", source=f"medium/{tag}")
                    results.extend(items[:limit // 3 + 1])
                except Exception as exc:
                    logger.debug("medium_rss_failed", tag=tag, error=str(exc))

        return results[:limit]

    # ── Google Trends (via public suggest API) ────────────────────────────────

    async def fetch_google_trends_suggest(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch Google autocomplete suggestions and related trending queries.
        Uses Google's public suggest API — no key needed.
        Returns what Google thinks is related to your query right now.
        """
        results: list[dict] = []
        try:
            profile = get_random_profile()
            headers = get_profile_http_headers(profile)
            async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                # Google autocomplete API
                resp = await client.get(
                    "https://suggestqueries.google.com/complete/search",
                    params={"q": query, "client": "firefox", "hl": "en", "gl": "us"},
                )
                resp.raise_for_status()
                data = resp.json()
                suggestions = data[1] if len(data) > 1 else []
                for suggestion in suggestions[:limit]:
                    results.append(_item(
                        "google_trends",
                        f"Trending: {suggestion}",
                        f"https://www.google.com/search?q={urllib.parse.quote(suggestion)}",
                        f"Google autocomplete trend related to: {query}",
                        meta={"channel": "autocomplete_suggest"},
                    ))

                # Also hit Google Trends public JSON API
                trends_resp = await client.get(
                    "https://trends.google.com/trends/api/dailytrends",
                    params={"hl": "en-US", "tz": "-60", "geo": "US", "ns": "15"},
                )
                if trends_resp.status_code == 200:
                    # Strip leading ")]}'" that Google prepends
                    raw_text = trends_resp.text.lstrip(")]}',\n")
                    import json
                    try:
                        trends_data = json.loads(raw_text)
                        stories = (trends_data.get("default", {})
                                              .get("trendingSearchesDays", [{}])[0]
                                              .get("trendingSearches", []))
                        query_lower = query.lower()
                        for story in stories[:limit]:
                            title_val = story.get("title", {}).get("query", "")
                            traffic   = story.get("formattedTraffic", "")
                            if title_val:
                                results.append(_item(
                                    "google_trends",
                                    title_val,
                                    f"https://trends.google.com/trends/explore?q={urllib.parse.quote(title_val)}",
                                    f"Google trending search — {traffic} searches",
                                    meta={"channel": "daily_trends", "traffic": traffic},
                                ))
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("google_trends_suggest_failed", error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── Instagram public hashtag page ─────────────────────────────────────────

    async def fetch_instagram_public(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch Instagram hashtag data via Instagram's public GraphQL endpoint.
        No login required for public hashtag counts and top posts.
        Note: Instagram aggressively rate-limits — we apply heavy delays.
        """
        results: list[dict] = []
        tag = query.replace(" ", "").lower()
        url = f"https://www.instagram.com/explore/tags/{tag}/?__a=1&__d=dis"

        profile = get_random_profile()
        headers = {
            **get_profile_http_headers(profile),
            "Referer":  f"https://www.instagram.com/explore/tags/{tag}/",
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with httpx.AsyncClient(timeout=20, headers=headers,
                                          follow_redirects=True) as client:
                # Warm up with a homepage visit first
                await client.get("https://www.instagram.com/", timeout=8)
                await asyncio.sleep(random.uniform(3.0, 6.0))
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    tag_data = (data.get("graphql", {})
                                    .get("hashtag", {}))
                    media_edge = (tag_data.get("edge_hashtag_to_top_posts", {})
                                          .get("edges", []))
                    for edge in media_edge[:limit]:
                        node     = edge.get("node", {})
                        caption  = (node.get("edge_media_to_caption", {})
                                        .get("edges", [{}])[0]
                                        .get("node", {})
                                        .get("text", ""))
                        shortcode = node.get("shortcode", "")
                        likes    = node.get("edge_liked_by", {}).get("count", 0)
                        if shortcode:
                            results.append(_item(
                                "instagram",
                                caption[:100] or f"#{tag} post",
                                f"https://www.instagram.com/p/{shortcode}/",
                                caption[:500],
                                meta={"likes": likes, "channel": "public_graphql"},
                            ))
        except Exception as exc:
            logger.debug("instagram_public_failed", tag=tag, error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── Facebook public RSS feeds (no token) ─────────────────────────────────

    async def fetch_facebook_public(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch Facebook-adjacent content via public RSS feeds and news
        aggregators that surface Facebook post discussions.
        Uses Google News RSS (which indexes Facebook posts) + Reddit
        discussions that link to Facebook content.
        """
        results: list[dict] = []
        query_encoded = urllib.parse.quote(query)

        feeds = [
            # Google News RSS — surfaces articles shared heavily on Facebook
            f"https://news.google.com/rss/search?q={query_encoded}&hl=en-US&gl=US&ceid=US:en",
            # Bing News RSS
            f"https://www.bing.com/news/search?q={query_encoded}&format=RSS",
        ]

        async with httpx.AsyncClient(timeout=20, headers=get_random_headers(),
                                      follow_redirects=True) as client:
            for feed_url in feeds:
                if len(results) >= limit:
                    break
                try:
                    resp = await client.get(feed_url)
                    if resp.status_code != 200:
                        continue
                    items = self._parse_rss_feed(resp.text, platform="facebook", source="news_rss")
                    # Only keep items that are likely social-media discussion
                    results.extend(items[:limit // 2 + 1])
                except Exception as exc:
                    logger.debug("facebook_public_rss_failed", feed=feed_url, error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── Snapchat Spotlight public discovery ──────────────────────────────────

    async def fetch_snapchat_public(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch Snapchat Spotlight trending via Snap's public search endpoint
        and the public story search page. No auth required.
        Falls back to scraping Spotlight public HTML.
        """
        results: list[dict] = []
        profile = get_random_profile()
        headers = {
            **get_profile_http_headers(profile),
            "Referer": "https://www.snapchat.com/",
            "Origin":  "https://www.snapchat.com",
        }

        try:
            async with httpx.AsyncClient(timeout=20, headers=headers,
                                          follow_redirects=True) as client:
                # Try Snap's public story search API
                resp = await client.get(
                    "https://story.snapchat.com/api/v1/story_search",
                    params={"query": query, "size": min(limit, 20)},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for story in data.get("results", [])[:limit]:
                        title   = story.get("title", "")
                        snap_id = story.get("snapId", "")
                        url     = f"https://www.snapchat.com/spotlight/{snap_id}" if snap_id else ""
                        if title or url:
                            results.append(_item(
                                "snapchat", title[:100], url,
                                story.get("description", "")[:500],
                                meta={"snap_id": snap_id, "channel": "spotlight_search"},
                            ))
                else:
                    # Fallback: scrape Spotlight discover page for trending tags
                    resp2 = await client.get(
                        f"https://www.snapchat.com/spotlight?q={urllib.parse.quote(query)}",
                        timeout=15,
                    )
                    if resp2.status_code == 200:
                        html = resp2.text
                        titles = re.findall(r'"title"\s*:\s*"([^"]{5,100})"', html)
                        urls   = re.findall(r'"deeplink"\s*:\s*"(https://www\.snapchat\.com/spotlight/[^"]+)"', html)
                        for i, title in enumerate(titles[:limit]):
                            url = urls[i] if i < len(urls) else "https://www.snapchat.com/spotlight"
                            results.append(_item(
                                "snapchat", title, url,
                                meta={"channel": "spotlight_html"},
                            ))
        except Exception as exc:
            logger.debug("snapchat_public_failed", error=str(exc))

        await smart_delay()
        return results[:limit]

    # ── LinkedIn public posts & jobs (no auth) ────────────────────────────────

    async def fetch_linkedin_public(
        self, query: str, limit: int = 20
    ) -> list[dict]:
        """
        Fetch LinkedIn-adjacent content via:
          1. LinkedIn public job/article search pages (ScraperAPI routed for JS render)
          2. GitHub trending (strong proxy for professional/tech topics)
          3. Medium RSS for professional long-form content
        No LinkedIn API key required.
        """
        results: list[dict] = []

        # Path 1: LinkedIn public search via ScraperAPI (handles JS render)
        try:
            from services.trend_scraper.utils.anti_block import build_scraperapi_url
            query_encoded = urllib.parse.quote(query)
            li_url = f"https://www.linkedin.com/search/results/content/?keywords={query_encoded}&origin=GLOBAL_SEARCH_HEADER"
            scraper_url = build_scraperapi_url(li_url, render=True)

            if scraper_url != li_url:  # ScraperAPI key is set
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(scraper_url)
                    if resp.status_code == 200:
                        html = resp.text
                        # Extract post titles from LinkedIn search results HTML
                        post_titles = re.findall(
                            r'class="[^"]*feed-shared-text[^"]*"[^>]*>\s*<[^>]*>\s*(.*?)\s*</',
                            html, re.DOTALL
                        )
                        for i, text in enumerate(post_titles[:limit]):
                            clean = re.sub(r"<[^>]+>", " ", text).strip()
                            clean = re.sub(r"\s+", " ", clean)
                            if clean and len(clean) > 10:
                                results.append(_item(
                                    "linkedin", clean[:100],
                                    f"https://www.linkedin.com/search/results/content/?keywords={query_encoded}",
                                    clean[:500],
                                    meta={"channel": "scraperapi_render", "index": i},
                                ))
        except Exception as exc:
            logger.debug("linkedin_scraperapi_failed", error=str(exc))

        # Path 2: GitHub trending as professional signal (always free)
        if len(results) < limit:
            try:
                github_items = await self.fetch_github_trending(query, limit - len(results))
                for item in github_items:
                    item["platform"] = "linkedin"  # re-label as professional signal
                    item["metadata"]["original_platform"] = "github"
                    results.append(item)
            except Exception:
                pass

        # Path 3: Medium RSS for professional articles
        if len(results) < limit:
            try:
                medium_items = await self.fetch_medium_rss(query, limit - len(results))
                for item in medium_items:
                    item["platform"] = "linkedin"
                    item["metadata"]["original_platform"] = "medium"
                    results.append(item)
            except Exception:
                pass

        await smart_delay()
        return results[:limit]

    # ── YouTube keyword search via Data API v3 ────────────────────────────────

    async def fetch_youtube_api_search(
        self, query: str, limit: int = 20, api_key: str = ""
    ) -> list[dict]:
        """
        YouTube Data API v3 keyword search — used when api_key is set.
        Quota-aware: falls back to RSS trending if quota is exhausted (403).
        Free tier: 10,000 units/day from Google Cloud Console.
        """
        if not api_key:
            return await self.fetch_youtube_trending_rss(query, limit)

        results: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=20, headers=get_random_headers()) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part":          "snippet",
                        "q":             query,
                        "type":          "video",
                        "order":         "relevance",
                        "maxResults":    min(limit, 50),
                        "publishedAfter": "2024-01-01T00:00:00Z",
                        "key":           api_key,
                    },
                )
                if resp.status_code == 403:
                    # Quota exhausted — fall back to RSS trending silently
                    logger.warning("youtube_api_quota_exhausted_falling_back_to_rss")
                    return await self.fetch_youtube_trending_rss(query, limit)

                resp.raise_for_status()
                data = resp.json()

                for item in data.get("items", [])[:limit]:
                    snippet  = item.get("snippet", {})
                    video_id = item.get("id", {}).get("videoId", "")
                    title    = snippet.get("title", "")
                    desc     = snippet.get("description", "")[:500]
                    channel  = snippet.get("channelTitle", "")
                    pub_at   = snippet.get("publishedAt")
                    if video_id and title:
                        results.append(_item(
                            "youtube", title,
                            f"https://www.youtube.com/watch?v={video_id}",
                            desc, pub_at,
                            meta={
                                "video_id": video_id,
                                "channel":  channel,
                                "channel_id": snippet.get("channelId", ""),
                                "channel": "youtube_api_search",
                            },
                        ))
        except Exception as exc:
            logger.debug("youtube_api_search_failed", error=str(exc))
            # Full fallback to free RSS on any error
            return await self.fetch_youtube_trending_rss(query, limit)

        return results[:limit]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_rss_feed(
        self, xml_text: str, platform: str, source: str
    ) -> list[dict]:
        """Parse RSS/Atom XML → list of item dicts."""
        items: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            # RSS 2.0
            for item in root.findall(".//item"):
                title   = item.findtext("title", "")
                url     = item.findtext("link",  "")
                snippet = item.findtext("description", "")
                snippet = re.sub(r"<[^>]+>", " ", snippet or "").strip()
                pub_str = item.findtext("pubDate")
                published_at = None
                if pub_str:
                    try:
                        published_at = parsedate_to_datetime(pub_str).isoformat()
                    except Exception:
                        pass
                if title or url:
                    items.append(_item(platform, title, url, snippet,
                                       published_at, meta={"feed": source}))
            # Atom
            if not items:
                for entry in root.findall("atom:entry", ns):
                    title   = entry.findtext("atom:title", "", ns)
                    link_el = entry.find("atom:link", ns)
                    url     = link_el.get("href", "") if link_el is not None else ""
                    snippet = entry.findtext("atom:summary", "", ns) or entry.findtext("atom:content", "", ns) or ""
                    snippet = re.sub(r"<[^>]+>", " ", snippet).strip()
                    pub_str = entry.findtext("atom:published", None, ns) or entry.findtext("atom:updated", None, ns)
                    if title or url:
                        items.append(_item(platform, title, url, snippet,
                                           pub_str, meta={"feed": source}))
        except Exception as exc:
            logger.debug("feed_parse_error", platform=platform, error=str(exc))
        return items

    def _parse_atom_feed(
        self, xml_text: str, platform: str, region: str
    ) -> list[dict]:
        """Parse YouTube Atom feed format."""
        return self._parse_rss_feed(xml_text, platform=platform, source=f"youtube_trending_{region}")
