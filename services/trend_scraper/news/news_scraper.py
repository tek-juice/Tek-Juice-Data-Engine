"""
DATA ENGINE — News RSS Scraper
Fetches trending articles from public RSS feeds.
Phase 2: News signal collection for trend monitoring.
"""

import structlog
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

logger = structlog.get_logger(__name__)

NEWS_FEEDS = [
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "Wired AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
]


class NewsScraper:
    """Fetches news articles from RSS feeds relevant to AI and data processing."""

    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch news items from RSS feeds.
        The `query` param is used for relevance filtering on fetched items.

        Returns:
            List of article dicts matching the query.
        """
        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            for feed in NEWS_FEEDS:
                try:
                    response = await client.get(feed["url"])
                    response.raise_for_status()
                    items = self._parse_rss(response.text, source=feed["name"])
                    # Simple keyword filter
                    filtered = [
                        i for i in items
                        if any(word.lower() in (i.get("title", "") + i.get("snippet", "")).lower()
                               for word in query.split()[:3])
                    ]
                    results.extend(filtered[:limit // len(NEWS_FEEDS) + 1])
                except Exception as exc:
                    logger.warning("news_feed_fetch_failed", feed=feed["name"], error=str(exc))

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
