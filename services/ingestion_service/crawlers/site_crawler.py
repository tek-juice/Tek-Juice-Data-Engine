"""
DATA ENGINE — Site Crawler
Crawls client websites for SEO/GEO auditing using SmartScraper.

Handles:
  - Static HTML sites (httpx, fast)
  - SPAs built with Next.js, React, Vue, Angular (Playwright, DOM hydration)
  - Cloudflare WAF-protected sites (stealth browser fingerprinting)
  - robots.txt compliance checking before crawling
  - Configurable depth and page limit

Used by:
  - Ingestion pipeline when source_type = 'html' and URL is provided
  - SEO engine for on-page analysis
  - GEO engine for entity extraction from live pages
"""

from __future__ import annotations

import asyncio
import structlog
from urllib.parse import urlparse, urljoin
from typing import AsyncGenerator

import httpx

from services.trend_scraper.utils.headless_browser import SmartScraper, PageContent

logger = structlog.get_logger(__name__)


class RobotsChecker:
    """
    Checks robots.txt to respect crawl permissions.
    Caches parsed robots.txt per domain.
    """

    def __init__(self) -> None:
        self._cache: dict[str, set[str]] = {}

    async def is_allowed(self, url: str, user_agent: str = "DataEngineBot") -> bool:
        """Return True if crawling this URL is permitted by robots.txt."""
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        if domain not in self._cache:
            await self._fetch_robots(domain)

        disallowed = self._cache.get(domain, set())
        path = parsed.path or "/"

        for disallowed_path in disallowed:
            if path.startswith(disallowed_path):
                logger.info("robots_txt_disallowed", url=url, path=disallowed_path)
                return False
        return True

    async def _fetch_robots(self, domain: str) -> None:
        """Fetch and parse robots.txt for a domain."""
        robots_url = f"{domain}/robots.txt"
        disallowed: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(robots_url)
                if resp.status_code == 200:
                    current_agent_match = False
                    for line in resp.text.splitlines():
                        line = line.strip()
                        if line.lower().startswith("user-agent:"):
                            agent = line.split(":", 1)[1].strip()
                            current_agent_match = agent in ("*", "DataEngineBot")
                        elif line.lower().startswith("disallow:") and current_agent_match:
                            path = line.split(":", 1)[1].strip()
                            if path:
                                disallowed.add(path)
        except Exception as exc:
            logger.debug("robots_txt_fetch_failed", domain=domain, error=str(exc))

        self._cache[domain] = disallowed


class SiteCrawler:
    """
    Multi-page crawler with robots.txt compliance and SPA support.

    Usage:
        crawler = SiteCrawler(max_pages=20, max_depth=3)
        async for page in crawler.crawl("https://client-website.com"):
            print(page.title, len(page.text))
    """

    def __init__(
        self,
        max_pages: int = 20,
        max_depth: int = 3,
        respect_robots: bool = True,
        same_domain_only: bool = True,
    ) -> None:
        self._max_pages      = max_pages
        self._max_depth      = max_depth
        self._respect_robots = respect_robots
        self._same_domain    = same_domain_only
        self._robots         = RobotsChecker()
        self._scraper        = SmartScraper()
        self._visited:  set[str] = set()
        self._queue:    list[tuple[str, int]] = []  # (url, depth)

    async def crawl(self, start_url: str) -> AsyncGenerator[PageContent, None]:
        """
        Crawl a website starting from start_url.
        Yields PageContent for each successfully crawled page.

        Respects robots.txt, stays on same domain, avoids revisiting URLs.
        Automatically uses headless browser for SPAs and Cloudflare pages.
        """
        parsed_start = urlparse(start_url)
        base_domain  = parsed_start.netloc

        self._queue   = [(start_url, 0)]
        self._visited = set()
        pages_crawled = 0

        while self._queue and pages_crawled < self._max_pages:
            url, depth = self._queue.pop(0)

            if url in self._visited:
                continue
            if depth > self._max_depth:
                continue

            # Normalise URL
            url = url.split("#")[0].rstrip("/")
            if url in self._visited:
                continue

            self._visited.add(url)

            # Check robots.txt
            if self._respect_robots:
                allowed = await self._robots.is_allowed(url)
                if not allowed:
                    continue

            # Fetch page
            try:
                logger.info("crawling_page", url=url, depth=depth, total=pages_crawled)
                content = await self._scraper.fetch(url)
                pages_crawled += 1
                yield content

                # Enqueue discovered links
                if depth < self._max_depth:
                    for link in content.links:
                        try:
                            parsed_link = urlparse(link)
                            # Stay on same domain if configured
                            if self._same_domain and parsed_link.netloc != base_domain:
                                continue
                            # Skip non-HTML resources
                            if any(link.endswith(ext) for ext in (
                                ".pdf", ".jpg", ".png", ".gif", ".svg",
                                ".css", ".js", ".ico", ".xml", ".zip",
                            )):
                                continue
                            norm = link.split("#")[0].rstrip("/")
                            if norm not in self._visited:
                                self._queue.append((norm, depth + 1))
                        except Exception:
                            continue

                # Small delay between pages to avoid overwhelming the server
                await asyncio.sleep(1.5)

            except Exception as exc:
                logger.warning("page_crawl_failed", url=url, error=str(exc))
                continue

        logger.info("crawl_complete", start_url=start_url, pages_crawled=pages_crawled)

    async def fetch_single(
        self,
        url: str,
        wait_for_selector: str | None = None,
        force_headless: bool = False,
    ) -> PageContent:
        """
        Fetch a single page. Convenience method for one-off audits.

        Args:
            url:               Target URL
            wait_for_selector: CSS selector to wait for (e.g. '[data-testid="main"]')
            force_headless:    Always use Playwright regardless of site type
        """
        return await self._scraper.fetch(
            url,
            force_headless=force_headless,
            wait_for_selector=wait_for_selector,
        )
