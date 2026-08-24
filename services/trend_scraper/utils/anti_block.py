"""
DATA ENGINE — Anti-Blocking Utilities
Provides proxy rotation, rate limiting, user-agent rotation,
fingerprint-consistent headers, and request delay jitter to avoid
scraper detection, TLS fingerprinting, and geo-blocks.

Upgrade summary (super-engine):
  - get_random_headers() now delegates to BrowserProfile for fully
    consistent UA + Accept + Sec-CH-UA header sets.
  - build_scraperapi_url() unchanged (ScraperAPI handles its own proxies).
  - ProxyRotator / RateLimiter unchanged.
  - New: get_stealth_headers() returns headers optimised for a specific
    target domain (adds Origin, Referer, and platform-specific fields).
"""

import asyncio
import random
import time
import urllib.parse
import structlog

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ─────────────────────────────────────────────
# User-Agent Pool — 20 strings, desktop + mobile
# ─────────────────────────────────────────────
USER_AGENTS = [
    # ── Chrome Windows ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # ── Chrome macOS ──
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # ── Chrome Linux ──
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Safari/537.36",
    # ── Firefox ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # ── Safari macOS ──
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # ── Edge ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # ── Chrome Android (mobile) ──
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.53 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    # ── Safari iOS (mobile) ──
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    # ── Samsung Internet ──
    "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36",
]

# Matching viewport sizes (width, height) for each UA above — index-aligned
_UA_VIEWPORTS = [
    (1920, 1080), (1440, 900),  (1366, 768),  # Chrome Windows
    (1440, 900),  (1280, 800),                 # Chrome macOS
    (1920, 1080), (1280, 720),                 # Chrome Linux
    (1920, 1080), (1440, 900),  (1280, 768),   # Firefox
    (2560, 1600), (1680, 1050),                # Safari macOS
    (1920, 1080), (1366, 768),                 # Edge
    (412,  915),  (360,  780),  (393,  873),   # Android Chrome
    (390,  844),  (375,  667),                 # iOS Safari
    (360,  780),                               # Samsung Internet
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

# ─────────────────────────────────────────────
# Domains that require JS rendering via ScraperAPI
# ─────────────────────────────────────────────
RENDER_DOMAINS: frozenset[str] = frozenset({
    "linkedin.com",
    "instagram.com",
    "tiktok.com",
    "snapchat.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "reddit.com",
    "pinterest.com",
    "quora.com",
})

# How long a quarantined proxy is excluded from rotation (seconds)
_PROXY_QUARANTINE_TTL = 600   # 10 minutes
# Number of consecutive failures before a proxy is quarantined
_PROXY_FAIL_THRESHOLD = 3


def _ua_index() -> int:
    """Pick a random UA index and use the matched viewport."""
    return random.randrange(len(USER_AGENTS))


def get_random_ua_and_viewport() -> tuple[str, tuple[int, int]]:
    """Return a (user_agent, (width, height)) pair that are fingerprint-consistent."""
    idx = _ua_index()
    return USER_AGENTS[idx], _UA_VIEWPORTS[idx]


class ProxyRotator:
    """
    Health-aware proxy rotator.

    - Cycles through the proxy pool in randomised order.
    - Tracks per-proxy consecutive failure counts.
    - Quarantines a proxy for _PROXY_QUARANTINE_TTL seconds after
      _PROXY_FAIL_THRESHOLD consecutive failures.
    - Automatically re-admits a proxy once the TTL expires.
    - Falls back to direct connection if all proxies are quarantined
      or no proxies are configured.
    """

    def __init__(self) -> None:
        self._proxies: list[str]       = list(settings.proxy_list)
        self._index: int               = 0
        self._failures: dict[str, int] = {}          # proxy → consecutive fail count
        self._quarantine: dict[str, float] = {}      # proxy → quarantine expiry (monotonic)

    def next_proxy(self) -> str | None:
        """Return the next healthy proxy, or None for a direct connection."""
        if not self._proxies:
            return None

        now = time.monotonic()
        # Attempt every proxy once before giving up
        for _ in range(len(self._proxies)):
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1

            expiry = self._quarantine.get(proxy)
            if expiry and now < expiry:
                logger.debug("proxy_skipped_quarantined", proxy=proxy[:30])
                continue  # still quarantined

            # Clear stale quarantine entry so failures reset cleanly
            if expiry and now >= expiry:
                del self._quarantine[proxy]
                self._failures[proxy] = 0

            logger.debug("proxy_selected", proxy=proxy[:30])
            return proxy

        # All proxies quarantined — fall back to direct connection
        logger.warning("all_proxies_quarantined_using_direct_connection")
        return None

    def mark_failed(self, proxy: str) -> None:
        """
        Record a failure against a proxy.
        Quarantines it if consecutive failures reach the threshold.
        """
        if not proxy:
            return
        count = self._failures.get(proxy, 0) + 1
        self._failures[proxy] = count
        if count >= _PROXY_FAIL_THRESHOLD:
            expiry = time.monotonic() + _PROXY_QUARANTINE_TTL
            self._quarantine[proxy] = expiry
            logger.warning(
                "proxy_quarantined",
                proxy=proxy[:40],
                failures=count,
                quarantine_seconds=_PROXY_QUARANTINE_TTL,
            )
        else:
            logger.warning("proxy_failure_recorded", proxy=proxy[:40], failures=count)

    def mark_success(self, proxy: str) -> None:
        """Reset failure count on a successful request."""
        if proxy:
            self._failures[proxy] = 0

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


def get_random_headers(domain: str = "") -> dict[str, str]:
    """
    Return a fully consistent set of HTTP headers for a given domain.
    Uses BrowserProfile to ensure UA, Accept, Sec-CH-UA, and language
    headers are all internally consistent — defeating header-fingerprint
    detectors that flag mismatched Accept/UA combinations.
    """
    try:
        from services.trend_scraper.utils.fingerprint_spoofer import (
            get_random_profile,
            get_profile_http_headers,
        )
        profile  = get_random_profile()
        headers  = get_profile_http_headers(profile)
        # Add domain-specific stealth headers
        if domain:
            origin_domain = domain if domain.startswith("http") else f"https://{domain}"
            headers["Origin"]  = origin_domain
            headers["Referer"] = origin_domain + "/"
        return headers
    except ImportError:
        # Fallback if fingerprint_spoofer is unavailable
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


def get_stealth_headers(target_url: str, referer: str = "") -> dict[str, str]:
    """
    Build maximally stealthy headers for a specific target URL.
    Adds Origin, Referer, and any platform-specific headers the target
    server expects from a real browser.

    Args:
        target_url: The URL being requested.
        referer:    Optional preceding page (builds realistic nav chain).
    """
    try:
        from services.trend_scraper.utils.fingerprint_spoofer import (
            get_random_profile,
            get_profile_http_headers,
        )
        profile = get_random_profile()
        headers = get_profile_http_headers(profile, referer=referer)
    except ImportError:
        headers = get_random_headers()

    parsed = urllib.parse.urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers["Origin"]  = origin
    if not referer:
        # Simulate a realistic referral chain (most traffic comes from Google)
        headers["Referer"] = random.choice([
            "https://www.google.com/",
            "https://www.google.co.uk/",
            origin + "/",
        ])
    return headers


# ── Request counter for periodic think-time pauses ───────────────────────────
_request_counter: int = 0
_THINK_TIME_EVERY  = 15   # inject a longer pause every N requests
_THINK_TIME_MIN    = 5.0  # seconds
_THINK_TIME_MAX    = 15.0


async def smart_delay() -> None:
    """
    Human-like inter-request delay.

    Uses a Gaussian distribution centred on the configured base delay so
    the timing histogram is indistinguishable from real browsing:
      - Most delays cluster around the base value.
      - Occasional short delays (< base) are allowed (human typing fast).
      - Enforces a hard minimum of 0.5 s to avoid accidental flooding.
      - Every _THINK_TIME_EVERY requests injects a longer 5–15 s think pause.
    """
    global _request_counter
    _request_counter += 1

    if _request_counter % _THINK_TIME_EVERY == 0:
        pause = random.uniform(_THINK_TIME_MIN, _THINK_TIME_MAX)
        logger.debug("smart_delay_think_time", seconds=round(pause, 1))
        await asyncio.sleep(pause)
        return

    base = settings.scraper_request_delay
    # Gaussian: mean=base, std=base*0.35 — allows delays slightly below base
    delay = random.gauss(base, base * 0.35)
    delay = max(0.5, delay)   # hard floor
    await asyncio.sleep(delay)


def _needs_render(url: str) -> bool:
    """Return True if the URL belongs to a domain that requires JS rendering."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
        return any(host.endswith(d) for d in RENDER_DOMAINS)
    except Exception:
        return False


def build_scraperapi_url(
    target_url: str,
    country: str = "us",
    render: bool | None = None,
) -> str:
    """
    Route any URL through ScraperAPI for automatic proxy rotation,
    CAPTCHA solving, and geo-targeting.

    Args:
        target_url: The URL you want to scrape.
        country:    2-letter country code for geo-targeting.
        render:     Force JS rendering on/off. If None, auto-detects based
                    on the target domain (True for LinkedIn, TikTok, etc.).

    Returns:
        ScraperAPI proxied URL, or the original URL if no key is configured.
    """
    if not settings.scraper_api_key:
        return target_url

    should_render = render if render is not None else _needs_render(target_url)

    params = urllib.parse.urlencode({
        "api_key":      settings.scraper_api_key,
        "url":          target_url,
        "country_code": country,
        "render":       "true" if should_render else "false",
    })
    return f"http://api.scraperapi.com?{params}"


def build_bing_scraperapi_url(query: str, country: str = "us", limit: int = 10) -> str:
    """
    Build a ScraperAPI URL that fetches Bing search results directly.
    No Microsoft Azure account or Bing API key required.

    Uses ScraperAPI's autoparse=true to return clean JSON instead of raw HTML.

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
        "render":       "false",   # Bing HTML parse doesn't need JS
    })
    return f"http://api.scraperapi.com?{scraper_params}"


def get_proxy_for_request() -> tuple[dict | None, str | None]:
    """
    Return (httpx_proxy_dict, raw_proxy_url) for the next healthy proxy.
    Both values are None when no proxies are configured.
    The raw URL is returned so callers can call mark_failed / mark_success.
    """
    proxy = _proxy_rotator.next_proxy()
    if not proxy:
        return None, None
    return {"http://": proxy, "https://": proxy}, proxy


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
