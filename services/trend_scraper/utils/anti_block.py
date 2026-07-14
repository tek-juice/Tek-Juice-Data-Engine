"""
DATA ENGINE — Anti-Blocking Utilities
Provides proxy rotation, rate limiting, user-agent rotation,
and request delay jitter to avoid scraper detection and geo-blocks.
"""

import asyncio
import random
import itertools
import structlog
from datetime import datetime, UTC

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────
# User-Agent Pool (rotated per request)
# ─────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

# ─────────────────────────────────────────────
# Accept-Language Pool (geo diversity)
# ─────────────────────────────────────────────
ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-CA,en;q=0.9",
    "en-AU,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
]


class ProxyRotator:
    """
    Rotates through a pool of proxies in round-robin order.
    Falls back to direct connection if no proxies configured.
    """

    def __init__(self) -> None:
        self._proxies = settings.proxy_list
        self._cycle = itertools.cycle(self._proxies) if self._proxies else None
        self._current: str | None = None

    def next_proxy(self) -> str | None:
        """Return the next proxy in rotation, or None for direct connection."""
        if not self._cycle:
            return None
        self._current = next(self._cycle)
        logger.debug("proxy_rotated", proxy=self._current[:30] + "..." if self._current else None)
        return self._current

    def mark_failed(self, proxy: str) -> None:
        """Log a failed proxy for monitoring."""
        logger.warning("proxy_failed", proxy=proxy[:30] + "..." if proxy else None)

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)


class RateLimiter:
    """
    Token bucket rate limiter — enforces max requests per minute.
    Shared across all scraper instances.
    """

    def __init__(self, rpm: int | None = None) -> None:
        self._rpm = rpm or settings.scraper_rate_limit_rpm
        self._min_interval = 60.0 / self._rpm
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait if needed to respect rate limit."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()


# Global shared instances
_proxy_rotator = ProxyRotator()
_rate_limiter = RateLimiter()


def get_random_headers() -> dict[str, str]:
    """Return randomised headers to avoid fingerprinting."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }


async def smart_delay() -> None:
    """
    Add jittered delay between requests to mimic human behaviour.
    Base delay from settings with ±50% random jitter.
    """
    base = settings.scraper_request_delay
    jitter = base * 0.5 * random.random()
    delay = base + jitter
    await asyncio.sleep(delay)


def build_scraperapi_url(target_url: str, country: str = "us") -> str:
    """
    Route any URL through ScraperAPI for automatic proxy rotation,
    CAPTCHA solving and geo-targeting.

    Args:
        target_url: The URL you want to scrape.
        country:    2-letter country code for geo-targeting (us, gb, de, jp, etc.)

    Returns:
        ScraperAPI proxied URL, or original URL if no key configured.
    """
    if not settings.scraper_api_key:
        return target_url

    import urllib.parse
    encoded = urllib.parse.quote_plus(target_url)
    return (
        f"http://api.scraperapi.com"
        f"?api_key={settings.scraper_api_key}"
        f"&url={encoded}"
        f"&country_code={country}"
        f"&render=false"
    )


def build_bing_scraperapi_url(query: str, country: str = "us", limit: int = 10) -> str:
    """
    Build a ScraperAPI URL that fetches Bing search results directly.
    No Microsoft Azure account or Bing API key required.

    Uses ScraperAPI's autoparse=true to return clean JSON instead of raw HTML.

    Endpoint format:
        http://api.scraperapi.com
          ?api_key=YOUR_KEY
          &url=https://www.bing.com/search?q=QUERY&count=LIMIT&setlang=en&cc=COUNTRY
          &autoparse=true
          &country_code=COUNTRY

    Args:
        query:   Search query string.
        country: 2-letter country code (us, gb, ca, au, de, fr, jp, in, br, za, ng, ae, sg).
        limit:   Number of results to request from Bing (max 50).

    Returns:
        Full ScraperAPI URL ready to GET, or empty string if no key configured.
    """
    if not settings.scraper_api_key:
        logger.warning("build_bing_scraperapi_url_called_without_key")
        return ""

    import urllib.parse

    bing_params = urllib.parse.urlencode({
        "q":       query,
        "count":   min(limit, 50),
        "setlang": "en",
        "cc":      country.upper(),
        "first":   1,
    })
    bing_url = f"https://www.bing.com/search?{bing_params}"

    scraper_params = urllib.parse.urlencode({
        "api_key":      settings.scraper_api_key,
        "url":          bing_url,
        "autoparse":    "true",
        "country_code": country,
        "render":       "false",
    })
    return f"http://api.scraperapi.com?{scraper_params}"


def get_proxy_for_request() -> dict | None:
    """
    Return httpx-compatible proxy dict for the next proxy in rotation.
    Returns None if no proxies configured (direct connection).
    """
    proxy = _proxy_rotator.next_proxy()
    if not proxy:
        return None
    return {"http://": proxy, "https://": proxy}


async def rate_limited_request() -> None:
    """Acquire rate limit slot before making a request."""
    await _rate_limiter.acquire()


def get_geo_targeted_params(country: str = "us") -> dict[str, str]:
    """
    Return query params for geo-targeted searches.
    Works with Google Custom Search API gl/hl params.

    Common country codes: us, gb, ca, au, de, fr, jp, in, br, za
    """
    country_to_lang = {
        "us": "en", "gb": "en", "ca": "en", "au": "en",
        "de": "de", "fr": "fr", "jp": "ja", "in": "hi",
        "br": "pt", "za": "en", "ng": "en", "ke": "en",
        "ae": "ar", "sg": "en", "mx": "es", "es": "es",
    }
    return {
        "gl": country,
        "hl": country_to_lang.get(country, "en"),
    }
