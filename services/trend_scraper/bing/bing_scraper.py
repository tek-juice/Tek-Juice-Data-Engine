"""
DATA ENGINE — Bing Web Search Scraper
Fetches trend data via Microsoft Bing Web Search API v7.
"""

import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

BING_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/search"


class BingScraper:
    """Fetches search results via Bing Web Search API."""

    def __init__(self) -> None:
        self._api_key = settings.bing_search_api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def fetch(self, query: str, limit: int = 10) -> list[dict]:
        """Fetch Bing search results for a query."""
        if not self._api_key:
            logger.warning("bing_scraper_not_configured")
            return []

        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.get(
                    BING_SEARCH_URL,
                    headers={"Ocp-Apim-Subscription-Key": self._api_key},
                    params={
                        "q": query,
                        "count": min(limit, 50),
                        "freshness": "Month",
                        "responseFilter": "Webpages",
                    },
                )
                response.raise_for_status()
                data = response.json()

                for item in data.get("webPages", {}).get("value", []):
                    results.append({
                        "title":           item.get("name", ""),
                        "url":             item.get("url", ""),
                        "snippet":         item.get("snippet", ""),
                        "published_at":    item.get("dateLastCrawled"),
                        "raw_content":     item.get("snippet", ""),
                        "relevance_score": 1.0,
                        "metadata": {
                            "source":       "bing",
                            "display_url":  item.get("displayUrl"),
                            "language":     item.get("language"),
                        },
                    })

                logger.debug("bing_search_complete", query=query, results=len(results))

            except httpx.HTTPStatusError as exc:
                logger.error("bing_search_http_error", status=exc.response.status_code)
                raise

        return results
