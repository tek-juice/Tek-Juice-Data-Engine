"""
DATA ENGINE — Google Search Scraper
Fetches trend data via Google Custom Search JSON API.
Phase 2: Search API consumption for external signal collection.
"""

import structlog
import httpx
from datetime import datetime, UTC
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


class GoogleScraper:
    """Fetches search results via Google Custom Search API."""

    def __init__(self) -> None:
        self._api_key = settings.google_search_api_key
        self._cx = settings.google_search_cx

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch search results for a query.

        Args:
            query: Search query string.
            limit: Max results to return (Google CSE max: 10 per request).

        Returns:
            List of result dicts with title, url, snippet, metadata.
        """
        if not self._api_key or not self._cx:
            logger.warning("google_scraper_not_configured")
            return []

        results = []
        num = min(limit, 10)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.get(
                    GOOGLE_SEARCH_URL,
                    params={
                        "key": self._api_key,
                        "cx": self._cx,
                        "q": query,
                        "num": num,
                        "dateRestrict": "m1",  # Last month
                    },
                )
                response.raise_for_status()
                data = response.json()

                for item in data.get("items", []):
                    results.append({
                        "title":          item.get("title", ""),
                        "url":            item.get("link", ""),
                        "snippet":        item.get("snippet", ""),
                        "published_at":   None,
                        "raw_content":    item.get("snippet", ""),
                        "relevance_score": 1.0,
                        "metadata": {
                            "source": "google",
                            "display_link": item.get("displayLink"),
                            "pagemap": item.get("pagemap", {}),
                        },
                    })

                logger.debug("google_search_complete", query=query, results=len(results))

            except httpx.HTTPStatusError as exc:
                logger.error("google_search_http_error", status=exc.response.status_code)
                raise

        return results
