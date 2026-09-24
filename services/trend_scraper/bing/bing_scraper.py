"""
DATA ENGINE — Bing Web Search Scraper
Fetches Bing search results via two methods, in priority order:

  Method 1 (Primary) — ScraperAPI + Bing HTML (no Azure account needed)
    Routes https://www.bing.com/search?q=... through ScraperAPI with
    autoparse=true so ScraperAPI returns clean structured JSON.
    Requires: SCRAPER_API_KEY in .env
    Endpoint: http://api.scraperapi.com?api_key=KEY&url=BING_URL&autoparse=true

  Method 2 (Fallback) — Microsoft Bing Web Search API v7
    Uses the legacy Azure Cognitive Services API.
    Requires: BING_SEARCH_API_KEY in .env
    Only used if ScraperAPI key is not configured.

  Method 3 (Last resort) — Direct Bing HTML scrape
    Falls back to direct httpx request with anti-blocking headers.
    No key required but may be rate-limited by Bing.

Priority: ScraperAPI → Microsoft API → Direct scrape
"""

import urllib.parse
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings
from services.trend_scraper.utils.anti_block import (
    get_random_headers,
    smart_delay,
    rate_limited_request,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# Bing search URL template
BING_SEARCH_URL  = "https://www.bing.com/search"
BING_API_URL     = "https://api.bing.microsoft.com/v7.0/search"
SCRAPERAPI_BASE  = "http://api.scraperapi.com"

# Countries to rotate for global coverage
GEO_TARGETS = ["gb"]


class BingScraper:
    """
    Fetches Bing search results using ScraperAPI (primary) or
    Microsoft Bing API (fallback), with direct HTML scrape as last resort.
    No Azure account required when SCRAPER_API_KEY is configured.
    """

    def __init__(self) -> None:
        self._scraper_api_key = settings.scraper_api_key
        self._bing_api_key    = settings.bing_search_api_key
        self._geo_idx         = 0

    def _next_geo(self) -> str:
        """Rotate through geo targets for global coverage."""
        geo = GEO_TARGETS[self._geo_idx % len(GEO_TARGETS)]
        self._geo_idx += 1
        return geo

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def fetch(
        self,
        query: str,
        limit: int = 10,
        country: str | None = None,
    ) -> list[dict]:
        """
        Fetch Bing search results for a query.

        Priority:
          1. ScraperAPI + Bing (no Azure needed)
          2. Microsoft Bing API v7 (needs BING_SEARCH_API_KEY)
          3. Direct Bing HTML scrape (no key, best-effort)

        Args:
            query:   Search query string.
            limit:   Max results to return.
            country: 2-letter country code for geo-targeting. Auto-rotates if None.
        """
        await rate_limited_request()
        geo = country or self._next_geo()

        # ── Method 1: ScraperAPI (recommended, no Azure needed) ──────────────
        if self._scraper_api_key:
            results = await self._fetch_via_scraperapi(query=query, limit=limit, geo=geo)
            if results:
                await smart_delay()
                return results
            logger.warning("bing_scraperapi_returned_empty_trying_fallback", query=query)

        # ── Method 2: Microsoft Bing API v7 (legacy, needs Azure key) ────────
        if self._bing_api_key:
            results = await self._fetch_via_microsoft_api(query=query, limit=limit)
            if results:
                await smart_delay()
                return results
            logger.warning("bing_microsoft_api_returned_empty_trying_direct", query=query)

        # ── Method 3: Direct HTML scrape (last resort, no key needed) ────────
        results = await self._fetch_direct(query=query, limit=limit, geo=geo)
        await smart_delay()
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Method 1 — ScraperAPI + Bing (Primary)
    # ─────────────────────────────────────────────────────────────────────────

    async def _fetch_via_scraperapi(
        self, query: str, limit: int, geo: str
    ) -> list[dict]:
        """
        Fetch Bing results through ScraperAPI.
        ScraperAPI handles:
          - Rotating residential proxies (bypasses Bing rate limits)
          - CAPTCHA solving
          - Geo-targeting via country_code param
          - HTML parsing with autoparse=true → returns clean JSON

        Endpoint format:
          http://api.scraperapi.com
            ?api_key=YOUR_KEY
            &url=https://www.bing.com/search?q=QUERY&count=NUM&setlang=en&cc=COUNTRY
            &autoparse=true
            &country_code=us
        """
        # Build the Bing URL to pass through ScraperAPI
        bing_params = urllib.parse.urlencode({
            "q":       query,
            "count":   min(limit, 50),
            "setlang": "en",
            "cc":      geo.upper(),
            "first":   1,
        })
        bing_url = f"{BING_SEARCH_URL}?{bing_params}"

        # Wrap in ScraperAPI endpoint
        scraper_params = urllib.parse.urlencode({
            "api_key":      self._scraper_api_key,
            "url":          bing_url,
            "autoparse":    "true",
            "country_code": geo,
            "render":       "false",
        })
        scraper_url = f"{SCRAPERAPI_BASE}?{scraper_params}"

        results = []
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.get(scraper_url)
                response.raise_for_status()
                data = response.json()

            # ScraperAPI autoparse returns Bing results in organic_results array
            organic = data.get("organic_results") or data.get("results") or []

            for item in organic[:limit]:
                results.append({
                    "title":           item.get("title", ""),
                    "url":             item.get("link") or item.get("url", ""),
                    "snippet":         item.get("snippet") or item.get("description", ""),
                    "published_at":    item.get("date"),
                    "raw_content":     item.get("snippet") or item.get("description", ""),
                    "relevance_score": 1.0,
                    "metadata": {
                        "source":      "bing",
                        "method":      "scraperapi",
                        "geo":         geo,
                        "display_url": item.get("displayed_link") or item.get("displayUrl", ""),
                    },
                })

            logger.debug(
                "bing_scraperapi_complete",
                query=query,
                geo=geo,
                results=len(results),
            )

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (403, 429):
                # 403 = out of ScraperAPI credits; 429 = rate limited — skip silently
                logger.warning("bing_scraperapi_quota_skip", status=status, query=query)
            else:
                logger.error("bing_scraperapi_http_error", status=status, query=query)
        except Exception as exc:
            logger.warning("bing_scraperapi_failed", error=str(exc), query=query)

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Method 2 — Microsoft Bing Web Search API v7 (Fallback)
    # ─────────────────────────────────────────────────────────────────────────

    async def _fetch_via_microsoft_api(self, query: str, limit: int) -> list[dict]:
        """
        Fetch via legacy Microsoft Bing Web Search API v7.
        Requires BING_SEARCH_API_KEY from Azure Cognitive Services.
        Used as fallback when ScraperAPI is not configured.
        """
        results = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    BING_API_URL,
                    headers={"Ocp-Apim-Subscription-Key": self._bing_api_key},
                    params={
                        "q":              query,
                        "count":          min(limit, 50),
                        "freshness":      "Month",
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
                        "source":      "bing",
                        "method":      "microsoft_api",
                        "display_url": item.get("displayUrl"),
                        "language":    item.get("language"),
                    },
                })

            logger.debug(
                "bing_microsoft_api_complete",
                query=query,
                results=len(results),
            )

        except httpx.HTTPStatusError as exc:
            logger.error(
                "bing_microsoft_api_http_error",
                status=exc.response.status_code,
                query=query,
            )
        except Exception as exc:
            logger.warning("bing_microsoft_api_failed", error=str(exc), query=query)

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Method 3 — Direct Bing HTML Scrape (Last Resort)
    # ─────────────────────────────────────────────────────────────────────────

    async def _fetch_direct(self, query: str, limit: int, geo: str) -> list[dict]:
        """
        Scrape Bing search results directly with stealth headers.
        No API key required. Uses anti-blocking headers and geo-targeting.
        May be rate-limited by Bing after sustained use.
        """
        results = []
        try:
            params = urllib.parse.urlencode({
                "q":       query,
                "count":   min(limit, 50),
                "setlang": "en",
                "cc":      geo.upper(),
            })
            url = f"{BING_SEARCH_URL}?{params}"
            headers = get_random_headers()

            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                html = response.text

            # Parse Bing HTML results
            results = self._parse_bing_html(html, geo=geo)
            logger.debug(
                "bing_direct_scrape_complete",
                query=query,
                geo=geo,
                results=len(results),
            )

        except Exception as exc:
            logger.warning("bing_direct_scrape_failed", error=str(exc), query=query)

        return results[:limit]

    @staticmethod
    def _parse_bing_html(html: str, geo: str = "us") -> list[dict]:
        """
        Parse Bing search result HTML with BeautifulSoup.
        Extracts title, URL, and snippet from standard result elements.
        """
        results = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            # Bing result cards are in <li class="b_algo"> elements
            for li in soup.select("li.b_algo"):
                # Title and URL from <h2><a>
                a_tag = li.select_one("h2 a")
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                url   = a_tag.get("href", "")

                # Snippet from <p> or <div class="b_caption">
                snippet_tag = li.select_one(".b_caption p") or li.select_one("p")
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

                if title and url and url.startswith("http"):
                    results.append({
                        "title":           title,
                        "url":             url,
                        "snippet":         snippet[:500],
                        "published_at":    None,
                        "raw_content":     snippet,
                        "relevance_score": 1.0,
                        "metadata": {
                            "source":  "bing",
                            "method":  "direct_html",
                            "geo":     geo,
                        },
                    })
        except Exception as exc:
            logger.warning("bing_html_parse_failed", error=str(exc))

        return results

    async def fetch_global(self, query: str, limit: int = 10) -> list[dict]:
        """
        Fetch Bing results from multiple geo targets for global coverage.
        Rotates through top 5 markets and deduplicates by URL.
        """
        all_results: dict[str, dict] = {}
        for geo in GEO_TARGETS[:5]:
            try:
                results = await self.fetch(query=query, limit=limit, country=geo)
                for r in results:
                    url = r.get("url", "")
                    if url and url not in all_results:
                        all_results[url] = r
            except Exception as exc:
                logger.warning("bing_geo_fetch_failed", geo=geo, error=str(exc))

        return list(all_results.values())[:limit]
