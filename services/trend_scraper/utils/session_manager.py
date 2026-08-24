"""
DATA ENGINE — Session Manager
Persistent browser session management with cookie jar warming, realistic
navigation history, and profile-consistent context reuse.

Why this matters:
  - Social platforms track session continuity. A fresh browser on every
    request is a bot signal. Real users have cookies, history, and JS state.
  - This manager keeps N warm sessions per platform and rotates them so
    each request looks like a returning user, not a cold bot.

Usage:
    async with SessionManager.get_session("instagram") as session:
        response = await session.get(url)
"""

from __future__ import annotations

import asyncio
import random
import time
import structlog
from dataclasses import dataclass, field
from typing import Any

import httpx

from services.trend_scraper.utils.fingerprint_spoofer import (
    BrowserProfile,
    get_random_profile,
    get_profile_http_headers,
)

logger = structlog.get_logger(__name__)

# Seconds a warm session is considered valid before being recycled
_SESSION_TTL = 1800  # 30 minutes
# Max warm sessions per platform domain
_MAX_SESSIONS_PER_DOMAIN = 3
# Warm-up URLs to visit before hitting target (builds realistic cookie state)
_WARMUP_PATHS: dict[str, list[str]] = {
    "twitter.com":    ["https://twitter.com", "https://twitter.com/explore"],
    "x.com":          ["https://x.com", "https://x.com/explore"],
    "instagram.com":  ["https://www.instagram.com/"],
    "tiktok.com":     ["https://www.tiktok.com/"],
    "facebook.com":   ["https://www.facebook.com/"],
    "linkedin.com":   ["https://www.linkedin.com/"],
    "reddit.com":     ["https://www.reddit.com/"],
    "youtube.com":    ["https://www.youtube.com/"],
    "snapchat.com":   ["https://www.snapchat.com/"],
}

# Realistic referer chains for major platforms
_REFERER_CHAINS: dict[str, list[str]] = {
    "twitter.com":   ["https://t.co", "https://google.com", "https://twitter.com"],
    "x.com":         ["https://t.co", "https://google.com", "https://x.com"],
    "instagram.com": ["https://www.google.com", "https://www.instagram.com"],
    "tiktok.com":    ["https://www.google.com", "https://vm.tiktok.com"],
    "facebook.com":  ["https://l.facebook.com", "https://m.facebook.com"],
    "linkedin.com":  ["https://www.google.com", "https://lnkd.in"],
    "reddit.com":    ["https://www.google.com", "https://old.reddit.com"],
    "youtube.com":   ["https://www.google.com", "https://youtu.be"],
}


@dataclass
class WarmSession:
    """A warmed-up httpx AsyncClient with cookies and fingerprint."""
    client:     httpx.AsyncClient
    profile:    BrowserProfile
    domain:     str
    created_at: float = field(default_factory=time.monotonic)
    request_count: int = 0

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > _SESSION_TTL

    @property
    def should_recycle(self) -> bool:
        """Recycle after 50 requests to avoid long-session fingerprinting."""
        return self.request_count >= 50 or self.is_expired


class SessionPool:
    """
    Per-domain pool of warm httpx sessions.
    Thread-safe via asyncio.Lock — one pool per domain, shared across scrapers.
    """

    def __init__(self, domain: str) -> None:
        self._domain  = domain
        self._sessions: list[WarmSession] = []
        self._lock    = asyncio.Lock()

    async def acquire(self, proxy: str | None = None) -> WarmSession:
        """
        Return a warm session. Creates one if pool is empty or all expired.
        Cleans up expired sessions automatically.
        """
        async with self._lock:
            # Evict expired sessions
            self._sessions = [s for s in self._sessions if not s.should_recycle]

            if self._sessions:
                # Round-robin from warm pool
                session = random.choice(self._sessions)
                session.request_count += 1
                logger.debug("session_pool_hit", domain=self._domain, pool_size=len(self._sessions))
                return session

            # Create a fresh warm session
            logger.debug("session_pool_creating_new", domain=self._domain)
            session = await self._create_warm_session(proxy)
            if len(self._sessions) < _MAX_SESSIONS_PER_DOMAIN:
                self._sessions.append(session)
            return session

    async def _create_warm_session(self, proxy: str | None = None) -> WarmSession:
        """Create a new httpx client, set headers, and warm up with homepage visit."""
        profile  = get_random_profile()
        headers  = get_profile_http_headers(profile)
        proxies  = {"http://": proxy, "https://": proxy} if proxy else None
        cookies  = httpx.Cookies()

        client = httpx.AsyncClient(
            headers=headers,
            cookies=cookies,
            proxies=proxies,
            timeout=30,
            follow_redirects=True,
            http2=True,   # HTTP/2 support — most platforms require it
        )

        # Warm-up: visit the domain homepage to get a session cookie
        warmup_urls = _WARMUP_PATHS.get(self._domain, [])
        for url in warmup_urls[:1]:   # Only first URL to keep it fast
            try:
                await asyncio.sleep(random.uniform(1.0, 2.5))
                await client.get(url, timeout=10)
                logger.debug("session_warmup_ok", domain=self._domain, url=url)
            except Exception as exc:
                logger.debug("session_warmup_skipped", domain=self._domain, error=str(exc))

        return WarmSession(client=client, profile=profile, domain=self._domain)

    async def close_all(self) -> None:
        """Close all client connections in the pool."""
        for session in self._sessions:
            try:
                await session.client.aclose()
            except Exception:
                pass
        self._sessions.clear()


class SessionManager:
    """
    Global session pool registry — one pool per domain.
    Use as an async context manager to get a warmed session.

    Example:
        mgr = SessionManager()
        async with mgr.get(domain="instagram.com") as session:
            resp = await session.client.get(url)
    """

    def __init__(self) -> None:
        self._pools: dict[str, SessionPool] = {}
        self._lock  = asyncio.Lock()

    def _get_domain(self, url_or_domain: str) -> str:
        """Extract bare domain from URL or pass through if already a domain."""
        if url_or_domain.startswith("http"):
            import urllib.parse
            return urllib.parse.urlparse(url_or_domain).netloc.replace("www.", "")
        return url_or_domain.replace("www.", "")

    async def get(
        self,
        domain: str,
        proxy: str | None = None,
    ) -> WarmSession:
        """Return a warm session for the given domain."""
        domain = self._get_domain(domain)
        async with self._lock:
            if domain not in self._pools:
                self._pools[domain] = SessionPool(domain)
        return await self._pools[domain].acquire(proxy)

    def get_referer(self, domain: str) -> str:
        """Return a realistic referer for the given domain."""
        domain = self._get_domain(domain)
        chain  = _REFERER_CHAINS.get(domain, ["https://www.google.com"])
        return random.choice(chain)

    async def close_all(self) -> None:
        """Close all sessions in all pools (call on shutdown)."""
        for pool in self._pools.values():
            await pool.close_all()


# Module-level singleton — shared across all scrapers
_session_manager = SessionManager()


async def get_warm_session(domain: str, proxy: str | None = None) -> WarmSession:
    """Convenience wrapper around the global session manager."""
    return await _session_manager.get(domain, proxy)


async def close_all_sessions() -> None:
    """Shutdown hook — close all warm sessions cleanly."""
    await _session_manager.close_all()
