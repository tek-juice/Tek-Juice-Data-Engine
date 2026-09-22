"""
DATA ENGINE — News RSS Scraper
Fetches trending articles from public RSS feeds.

Strategy:
  1. Query-specific Google News RSS (no key, always works) — primary
  2. Hacker News search RSS (no key, always works) — primary
  3. Curated AI/tech RSS feeds — secondary enrichment
  4. Falls through unfiltered if keyword filter yields nothing
     (better to return any news than 0 results)
"""

import re
import urllib.parse
import structlog
import httpx
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

logger = structlog.get_logger(__name__)

# ── Always-on feeds (no API key, no auth, no bot-detection issues) ────────────
# Google News RSS and HN search work reliably without any credentials.
QUERY_FEEDS = [
    # Google News RSS — returns current news for any search query
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    # Hacker News Algolia search RSS (tech/AI signal)
    "https://hn.algolia.com/api/v1/search_by_date?query={query}&tags=story&hitsPerPage={limit}",
]

# ── Curated topic feeds — enrich after query-specific results ─────────────────
CURATED_FEEDS = [
    {"name": "TechCrunch AI",  "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "The Verge AI",   "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "Wired AI",       "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
    {"name": "MIT Tech Review","url": "https://www.technologyreview.com/feed/"},
    {"name": "ArXiv CS.AI",    "url": "https://rss.arxiv.org/rss/cs.AI"},
]


class NewsScraper:
    """
    Fetches news articles from RSS feeds.

    Primary path: Google News RSS + HN search — these always return results
    for any query without API keys. Curated feeds enrich the signal.
    Falls through unfiltered as a last resort so 0-result runs are eliminated.
    """

    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch news items for the given query.

        Priority:
          1. Google News RSS (query-specific, no key)
          2. Hacker News Algolia search (query-specific, no key)
          3. Curated AI/tech RSS feeds (keyword-filtered, then unfiltered fallback)
        """
        results: list[dict] = []
        query_enc = urllib.parse.quote(query)

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:

            # ── 1. Google News RSS (no key needed) ────────────────────────────
            gnews_url = (
                f"https://news.google.com/rss/search"
                f"?q={query_enc}&hl=en-US&gl=US&ceid=US:en"
            )
            try:
                resp = await client.get(gnews_url)
                if resp.status_code == 200:
                    items = self._parse_rss(resp.text, source="google_news")
                    results.extend(items[: limit // 2 + 1])
                    logger.debug("news_google_rss_ok", query=query, count=len(items))
            except Exception as exc:
                logger.warning("news_google_rss_failed", error=str(exc))

            # ── 2. Hacker News Algolia search (no key) ────────────────────────
            if len(results) < limit:
                hn_url = (
                    f"https://hn.algolia.com/api/v1/search_by_date"
                    f"?query={query_enc}&tags=story&hitsPerPage={limit}"
                )
                try:
                    resp = await client.get(hn_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        for hit in data.get("hits", [])[: limit // 2 + 1]:
                            title   = hit.get("title", "")
                            url     = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                            snippet = hit.get("story_text") or hit.get("comment_text") or ""
                            snippet = re.sub(r"<[^>]+>", " ", snippet).strip()[:500]
                            pub_at  = hit.get("created_at")
                            if title:
                                results.append({
                                    "title":           title,
                                    "url":             url,
                                    "snippet":         snippet,
                                    "published_at":    pub_at,
                                    "raw_content":     snippet,
                                    "relevance_score": 1.0,
                                    "metadata":        {"source": "news", "feed": "hackernews"},
                                })
                        logger.debug("news_hn_ok", query=query, count=len(data.get("hits", [])))
                except Exception as exc:
                    logger.warning("news_hn_failed", error=str(exc))

            # ── 3. Curated RSS feeds (keyword-filtered, unfiltered fallback) ──
            for feed in CURATED_FEEDS:
                if len(results) >= limit:
                    break
                try:
                    resp = await client.get(feed["url"])
                    resp.raise_for_status()
                    items = self._parse_rss(resp.text, source=feed["name"])
                    filtered = [
                        i for i in items
                        if any(
                            w.lower() in (i.get("title", "") + i.get("snippet", "")).lower()
                            for w in query.split()[:3]
                        )
                    ]
                    # Use filtered if we got matches; otherwise take any items
                    # so a niche query never produces 0 from a live feed.
                    bucket = filtered if filtered else items
                    results.extend(bucket[: limit // len(CURATED_FEEDS) + 1])
                except Exception as exc:
                    logger.warning("news_feed_fetch_failed", feed=feed["name"], error=str(exc))

        logger.info("news_scraper_complete", query=query, count=len(results[:limit]))
        return results[:limit]

    @staticmethod
    def _parse_rss(xml_content: str, source: str) -> list[dict]:
        """Parse RSS XML and return list of article dicts."""
        items = []
        try:
            root = ET.fromstring(xml_content)
            channel = root.find("channel")
            if channel is None:
                channel = root  # Atom feed

            for item in channel.findall("item"):
                title = item.findtext("title", "")
                url = item.findtext("link", "")
                snippet = item.findtext("description", "")
                pub_date_str = item.findtext("pubDate")

                published_at = None
                if pub_date_str:
                    try:
                        published_at = parsedate_to_datetime(pub_date_str).isoformat()
                    except Exception:
                        pass

                # Strip HTML from snippet
                import re
                snippet = re.sub(r"<[^>]+>", " ", snippet or "").strip()

                items.append({
                    "title":           title,
                    "url":             url,
                    "snippet":         snippet[:500],
                    "published_at":    published_at,
                    "raw_content":     snippet,
                    "relevance_score": 1.0,
                    "metadata":        {"source": "news", "feed": source},
                })
        except ET.ParseError as exc:
            logger.warning("rss_parse_error", error=str(exc))

        return items
