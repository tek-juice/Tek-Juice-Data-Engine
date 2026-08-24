"""
DATA ENGINE — Headless Browser Engine
Playwright-based crawler for SPA/CSR sites and Cloudflare-protected pages.

Handles:
  - Single Page Applications (Next.js, React, Vue) that return empty HTML shells
    to standard httpx requests — waits for full DOM hydration before extraction
  - Cloudflare WAF / Bot Management bypass via stealth browser fingerprinting
  - JavaScript-rendered content that httpx cannot capture
  - Dynamic lazy-loaded content via scroll simulation

Usage:
    async with HeadlessBrowser() as browser:
        content = await browser.fetch("https://example.com")
        html    = content.html
        text    = content.text
        links   = content.links
"""

from __future__ import annotations

import asyncio
import urllib.parse
import structlog
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from configs.settings import get_settings
from services.trend_scraper.utils.anti_block import (
    USER_AGENTS,
    get_random_headers,
    get_random_ua_and_viewport,
    _proxy_rotator,
)
from services.trend_scraper.utils.fingerprint_spoofer import (
    get_random_profile,
    get_profile_http_headers,
    BrowserProfile,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# How long to wait for DOM network idle after page load (ms)
DOM_IDLE_TIMEOUT    = 15_000
# How long to wait for a specific selector before giving up (ms)
SELECTOR_TIMEOUT    = 8_000
# Maximum page load timeout (ms)
PAGE_LOAD_TIMEOUT   = 30_000
# Scroll pause between steps to trigger lazy-load (ms)
SCROLL_PAUSE_MS     = 800


@dataclass
class PageContent:
    """Structured result from a headless browser fetch."""
    url:        str
    html:       str
    text:       str
    title:      str
    links:      list[str]   = field(default_factory=list)
    metadata:   dict[str, Any] = field(default_factory=dict)
    was_js_rendered: bool   = False
    cloudflare_bypassed: bool = False


def _parse_proxy_for_playwright(proxy_url: str) -> dict[str, str]:
    """
    Convert a proxy URL (possibly containing credentials) into the dict
    that Playwright's launch(proxy=...) option expects.

    Supports both:
      - http://host:port               → {"server": "http://host:port"}
      - http://user:pass@host:port     → {"server": "...", "username": ..., "password": ...}
    """
    parsed = urllib.parse.urlparse(proxy_url)
    server = urllib.parse.urlunparse(parsed._replace(netloc=parsed.hostname + (f":{parsed.port}" if parsed.port else "")))
    result: dict[str, str] = {"server": server}
    if parsed.username:
        result["username"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        result["password"] = urllib.parse.unquote(parsed.password)
    return result


class HeadlessBrowser:
    """
    Async context manager wrapping Playwright Chromium.
    Handles stealth mode, DOM hydration waiting, and scroll simulation.

    Each instantiation pulls a fresh proxy from the health-aware rotator
    so headless requests cycle through the full pool rather than always
    hitting the same IP.

    Example:
        async with HeadlessBrowser() as browser:
            content = await browser.fetch("https://spa-site.com/page")
    """

    def __init__(
        self,
        headless: bool = True,
        proxy: str | None = None,
        stealth: bool = True,
    ) -> None:
        self._headless = headless
        self._stealth  = stealth
        # Use the caller-supplied proxy, or draw from the rotation pool.
        # _proxy_rotator.next_proxy() is health-aware: it skips quarantined IPs.
        self._proxy_url: str | None = proxy or _proxy_rotator.next_proxy()
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "HeadlessBrowser":
        self._playwright = await async_playwright().start()
        launch_opts: dict[str, Any] = {
            "headless": self._headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-web-security",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
        }
        if self._proxy_url:
            # Playwright needs credentials parsed out of the URL separately
            launch_opts["proxy"] = _parse_proxy_for_playwright(self._proxy_url)

        self._browser = await self._playwright.chromium.launch(**launch_opts)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch(
        self,
        url: str,
        wait_for_selector: str | None = None,
        scroll_to_bottom: bool = False,
        extract_links: bool = True,
    ) -> PageContent:
        """
        Fetch a URL using a headless browser with full JS execution.

        Args:
            url:               Target URL to fetch.
            wait_for_selector: CSS selector to wait for before extracting content.
                               Use for SPAs where content loads after a specific element renders.
            scroll_to_bottom:  Simulate scrolling to trigger lazy-loaded content.
            extract_links:     Whether to extract all href links from the page.

        Returns:
            PageContent with full rendered HTML, text, title, and links.
        """
        if not self._browser:
            raise RuntimeError("HeadlessBrowser must be used as async context manager")

        # Use a fully consistent browser profile (UA + viewport + fingerprint)
        profile: BrowserProfile = getattr(self, "_profile", None) or get_random_profile()
        self._profile = profile

        context: BrowserContext = await self._browser.new_context(
            user_agent=profile.user_agent,
            viewport={"width": profile.viewport_w, "height": profile.viewport_h},
            locale=profile.language,
            timezone_id=profile.timezone,
            color_scheme="light",
            device_scale_factor=profile.pixel_ratio,
            # Profile-consistent headers
            extra_http_headers={
                "Accept-Language": profile.accept_language,
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                **({"Sec-CH-UA": profile.sec_ch_ua,
                    "Sec-CH-UA-Mobile": "?1" if "Mobile" in profile.user_agent else "?0",
                    "Sec-CH-UA-Platform": profile.sec_ch_ua_platform,
                    } if profile.sec_ch_ua else {}),
            },
        )

        if self._stealth:
            # Inject full fingerprint spoofer (canvas, WebGL, audio, plugins, etc.)
            await context.add_init_script(profile.to_js_init_script())

        page: Page = await context.new_page()
        was_js_rendered    = False
        cloudflare_bypassed = False

        try:
            await page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

            # Detect Cloudflare challenge page
            cf_challenge = await page.query_selector("#challenge-form, .cf-browser-verification")
            if cf_challenge:
                logger.info("cloudflare_challenge_detected", url=url)
                # Wait for CF to auto-solve (JS challenge usually resolves in 5s)
                await page.wait_for_load_state("networkidle", timeout=DOM_IDLE_TIMEOUT)
                cf_still_present = await page.query_selector("#challenge-form")
                cloudflare_bypassed = cf_still_present is None
                logger.info("cloudflare_bypass_result", url=url, success=cloudflare_bypassed)

            # Wait for DOM network idle — critical for SPAs (React/Next.js/Vue)
            try:
                await page.wait_for_load_state("networkidle", timeout=DOM_IDLE_TIMEOUT)
                was_js_rendered = True
            except PlaywrightTimeoutError:
                logger.debug("network_idle_timeout", url=url)

            # Wait for specific selector if provided (e.g. main content container)
            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=SELECTOR_TIMEOUT)
                except PlaywrightTimeoutError:
                    logger.debug("selector_timeout", url=url, selector=wait_for_selector)

            # Scroll to trigger lazy-loaded content
            if scroll_to_bottom:
                await self._scroll_page(page)

            # Extract content
            html  = await page.content()
            title = await page.title()
            text  = await page.evaluate("""
                () => {
                    // Remove script, style, nav, footer noise
                    ['script','style','nav','footer','header','aside'].forEach(tag => {
                        document.querySelectorAll(tag).forEach(el => el.remove());
                    });
                    return document.body ? document.body.innerText.trim() : '';
                }
            """)

            links: list[str] = []
            if extract_links:
                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]'))
                         .map(a => a.href)
                         .filter(h => h.startsWith('http'))
                         .slice(0, 100)
                """)

            if self._proxy_url:
                _proxy_rotator.mark_success(self._proxy_url)

            return PageContent(
                url=url,
                html=html,
                text=text[:50_000],
                title=title,
                links=links,
                metadata={
                    "user_agent":  profile.user_agent,
                    "viewport":    f"{profile.viewport_w}x{profile.viewport_h}",
                    "proxy_used":  bool(self._proxy_url),
                    "profile":     profile.name,
                },
                was_js_rendered=was_js_rendered,
                cloudflare_bypassed=cloudflare_bypassed,
            )

        except PlaywrightTimeoutError as exc:
            if self._proxy_url:
                _proxy_rotator.mark_failed(self._proxy_url)
            logger.warning("headless_page_timeout", url=url, error=str(exc))
            raise
        except Exception as exc:
            if self._proxy_url:
                _proxy_rotator.mark_failed(self._proxy_url)
            logger.error("headless_fetch_error", url=url, error=str(exc))
            raise
        finally:
            await page.close()
            await context.close()

    @staticmethod
    async def _scroll_page(page: Page) -> None:
        """Scroll the page in steps to trigger lazy-loaded content."""
        scroll_height = await page.evaluate("document.body.scrollHeight")
        viewport_height = 800
        current = 0
        while current < scroll_height:
            current += viewport_height
            await page.evaluate(f"window.scrollTo(0, {current})")
            await asyncio.sleep(SCROLL_PAUSE_MS / 1000)
        # Scroll back to top
        await page.evaluate("window.scrollTo(0, 0)")


class SmartScraper:
    """
    Intelligent scraper that automatically selects between:
      - httpx (fast, for API endpoints and simple HTML pages)
      - HeadlessBrowser (for SPAs, Cloudflare-protected, JS-rendered pages)

    Detection heuristics:
      - Known SPA frameworks in HTML → use headless
      - Cloudflare headers in response → use headless
      - Empty body / minimal content → retry with headless
      - Content-Type not text/html → use httpx only
    """

    # Sites/patterns known to require headless rendering
    HEADLESS_DOMAINS = {
        "linkedin.com",
        "instagram.com",
        "tiktok.com",
        "snapchat.com",
        "twitter.com",
        "x.com",
        "facebook.com",
    }

    # Cloudflare challenge indicators in response body
    CF_INDICATORS = [
        "cf-browser-verification",
        "challenge-form",
        "Checking your browser",
        "DDoS protection by Cloudflare",
        "cf_clearance",
        "Just a moment",
    ]

    # SPA framework indicators in HTML
    SPA_INDICATORS = [
        'id="__next"',          # Next.js
        'id="__nuxt"',          # Nuxt.js
        'id="app"',             # Vue / generic
        'data-reactroot',       # React
        'ng-version',           # Angular
        "_next/static",         # Next.js static assets
    ]

    def __init__(self) -> None:
        self._httpx_client = None

    async def fetch(
        self,
        url: str,
        force_headless: bool = False,
        wait_for_selector: str | None = None,
    ) -> PageContent:
        """
        Fetch a URL using the best strategy automatically.

        Args:
            url:               Target URL
            force_headless:    Skip detection and always use headless browser
            wait_for_selector: CSS selector to wait for (headless mode only)
        """
        import httpx
        from urllib.parse import urlparse

        domain = urlparse(url).netloc.replace("www.", "")

        # Known headless domains — skip detection
        if force_headless or any(d in domain for d in self.HEADLESS_DOMAINS):
            return await self._headless_fetch(url, wait_for_selector)

        # Try httpx first
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": get_random_profile().user_agent,
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    },
                    follow_redirects=True,
                )

                # Cloudflare detected in headers or body
                cf_header = resp.headers.get("cf-ray") or resp.headers.get("server", "").lower() == "cloudflare"
                body = resp.text
                cf_in_body = any(ind in body for ind in self.CF_INDICATORS)
                spa_detected = any(ind in body for ind in self.SPA_INDICATORS)
                empty_body = len(body.strip()) < 500

                if cf_header or cf_in_body or spa_detected or empty_body:
                    logger.info(
                        "switching_to_headless",
                        url=url,
                        reason="cloudflare" if (cf_header or cf_in_body) else "spa" if spa_detected else "empty_body",
                    )
                    return await self._headless_fetch(url, wait_for_selector)

                # httpx result is good — wrap in PageContent
                import re
                text = re.sub(r"<[^>]+>", " ", body).strip()
                title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
                links = re.findall(r'href="(https?://[^"]+)"', body)

                return PageContent(
                    url=url,
                    html=body,
                    text=text[:50_000],
                    title=title_match.group(1).strip() if title_match else "",
                    links=links[:100],
                    was_js_rendered=False,
                )

        except Exception as exc:
            logger.warning("httpx_fetch_failed_trying_headless", url=url, error=str(exc))
            return await self._headless_fetch(url, wait_for_selector)

    async def _headless_fetch(
        self, url: str, wait_for_selector: str | None = None
    ) -> PageContent:
        async with HeadlessBrowser() as browser:
            return await browser.fetch(
                url,
                wait_for_selector=wait_for_selector,
                scroll_to_bottom=True,
                extract_links=True,
            )
