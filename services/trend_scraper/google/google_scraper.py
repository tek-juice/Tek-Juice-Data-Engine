"""
DATA ENGINE — Google Search Scraper
Fetches trend data via Google Custom Search JSON API.
Includes rate limiting, geo-targeting, and proxy support.
"""

import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings
from services.trend_scraper.utils.anti_block import (
    rate_limited_request,
    smart_delay,
    get_geo_targeted_params,
    build_scraperapi_url,
    get_proxy_for_request,
)
from services.trend_scraper.utils.headless_browser import SmartScraper

logger = structlog.get_logger(__name__)
settings = get_settings()

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

# Countries to rotate through for global coverage
GEO_TARGETS = ["us", "gb", "ca", "au", "in", "de", "fr", "jp", "br", "za", "ng", "ae", "sg"]


class GoogleScraper:
    """Fetches search results via Google Custom Search API with anti-blocking."""

    def __init__(self) -> None:
        self._api_key = settings.google_search_api_key
        self._cx = settings.google_search_cx
        self._geo_cycle = iter(GEO_TARGETS)

    def _next_geo(self) -> str:
        """Rotate through geo targets for global coverage."""
        try:
            return next(self._geo_cycle)
        except StopIteration:
            self._geo_cycle = iter(GEO_TARGETS)
            return next(self._geo_cycle)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def fetch(self, query: str, limit: int = 10, country: str | None = None) -> list[dict]:
        """
        Fetch search results for a query with geo-targeting and rate limiting.

        Args:
            query:   Search query string.
            limit:   Max results (Google CSE max: 10 per request).
            country: 2-letter country code for geo-targeting. Auto-rotates if None.
        """
        if not self._api_key or not self._cx:
            logger.warning("google_scraper_not_configured")
            return []

        await rate_limited_request()

        geo = country or self._next_geo()
        geo_params = get_geo_targeted_params(geo)
        proxies, proxy_url = get_proxy_for_request()

        results = []
        try:
            async with httpx.AsyncClient(timeout=30, proxy=proxies) as client:
                response = await client.get(
                    GOOGLE_SEARCH_URL,
                    params={
                        "key":          self._api_key,
                        "cx":           self._cx,
                        "q":            query,
                        "num":          min(limit, 10),
                        "dateRestrict": "m1",
                        **geo_params,
                    },
                )
                response.raise_for_status()

                for item in response.json().get("items", []):
                    results.append({
                        "title":           item.get("title", ""),
                        "url":             item.get("link", ""),
                        "snippet":         item.get("snippet", ""),
                        "published_at":    None,
                        "raw_content":     item.get("snippet", ""),
                        "relevance_score": 1.0,
                        "metadata": {
                            "source":       "google",
                            "geo":          geo,
                            "display_link": item.get("displayLink"),
                            "pagemap":      item.get("pagemap", {}),
                        },
                    })

                logger.debug("google_search_complete", query=query, geo=geo, results=len(results))
                if proxy_url:
                    from services.trend_scraper.utils.anti_block import _proxy_rotator
                    _proxy_rotator.mark_success(proxy_url)

        except httpx.HTTPStatusError as exc:
            if proxy_url:
                from services.trend_scraper.utils.anti_block import _proxy_rotator
                _proxy_rotator.mark_failed(proxy_url)
            logger.error("google_search_http_error", status=exc.response.status_code, geo=geo)
            raise

        await smart_delay()
        return results

    async def fetch_page_content(self, url: str) -> str:
        """
        Fetch full rendered content from a URL using SmartScraper.
        Automatically uses headless browser for SPAs and Cloudflare-protected pages.
        Used by the SEO/GEO engine to audit client websites.
        """
        scraper = SmartScraper()
        content = await scraper.fetch(url)
        logger.info(
            "page_content_fetched",
            url=url,
            js_rendered=content.was_js_rendered,
            cf_bypassed=content.cloudflare_bypassed,
            text_length=len(content.text),
        )
        return content.text

    async def fetch_global(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch results from multiple geo targets for true global coverage.
        Rotates through all configured countries and deduplicates by URL.
        """
        all_results: dict[str, dict] = {}
        for country in GEO_TARGETS[:5]:  # Top 5 markets by default
            try:
                results = await self.fetch(query=query, limit=limit, country=country)
                for r in results:
                    url = r.get("url", "")
                    if url and url not in all_results:
                        all_results[url] = r
            except Exception as exc:
                logger.warning("google_geo_fetch_failed", country=country, error=str(exc))

        return list(all_results.values())[:limit]
