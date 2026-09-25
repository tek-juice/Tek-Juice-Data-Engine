"""
DATA ENGINE — SearXNG SERP Rank Tracker

Fetches current organic search rankings from the internal SearXNG instance
and persists daily ranking snapshots.

SearXNG is the sole SERP provider. No paid SEO/SERP provider is required.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

SEARXNG_TIMEOUT = 30.0
SEARXNG_MAX_CONCURRENT = 5
SEARXNG_MAX_RESULTS = 20


@dataclass
class RankSnapshot:
    """A single keyword ranking snapshot."""

    tenant_id: str
    domain: str
    keyword: str
    language: str
    position: int | None
    url: str | None
    title: str | None
    snippet: str | None
    engines: list[str] = field(default_factory=list)
    score: float | None = None
    rank_found: bool = False
    results_checked: int = 0
    snapshot_date: date = field(default_factory=date.today)
    raw_serp_item: dict = field(default_factory=dict)


@dataclass
class RankTrackingResult:
    """Results from a batch of SearXNG rank checks."""

    snapshots: list[RankSnapshot]
    tracked: int
    not_ranked: int
    errors: int
    api_calls_used: int


class RankTracker:
    """Track domain rankings through the internal SearXNG instance."""

    def __init__(self, searxng_url: str = "http://localhost:8080") -> None:
        self._base_url = searxng_url.rstrip("/")
        self._enabled = bool(searxng_url)

        if not self._enabled:
            logger.warning(
                "rank_tracker_disabled",
                reason="SEARXNG_URL is not configured",
            )

    async def track_batch(
        self,
        items: list[dict],
    ) -> RankTrackingResult:
        """
        Fetch rankings for:

        {
            "tenant_id": "...",
            "domain": "example.com",
            "keyword": "example search query",
            "language": "en"
        }
        """

        if not self._enabled or not items:
            return RankTrackingResult(
                snapshots=[],
                tracked=0,
                not_ranked=0,
                errors=0,
                api_calls_used=0,
            )

        sem = asyncio.Semaphore(SEARXNG_MAX_CONCURRENT)

        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            tasks = [
                self._fetch_rank(
                    client=client,
                    sem=sem,
                    item=item,
                )
                for item in items
            ]

            results = await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        snapshots: list[RankSnapshot] = []
        errors = 0
        not_ranked = 0

        for result in results:
            if isinstance(result, Exception):
                errors += 1
                logger.warning(
                    "rank_fetch_batch_error",
                    error=str(result),
                )
                continue

            if result is None:
                errors += 1
                continue

            snapshots.append(result)

            if result.rank_found:
                continue

            not_ranked += 1

        return RankTrackingResult(
            snapshots=snapshots,
            tracked=len(snapshots),
            not_ranked=not_ranked,
            errors=errors,
            api_calls_used=len(items) - errors,
        )

    async def persist(
        self,
        snapshots: list[RankSnapshot],
    ) -> int:
        """Persist ranking snapshots to the rank_tracking table."""

        if not snapshots:
            return 0

        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        inserted = 0

        async with AsyncSessionLocal() as session:
            for snapshot in snapshots:
                await session.execute(
                    text(
                        """
                        INSERT INTO rank_tracking (
                            tenant_id,
                            domain,
                            keyword,
                            language,
                            position,
                            ranking_url,
                            title,
                            snippet,
                            engines,
                            score,
                            rank_found,
                            results_checked,
                            snapshot_date,
                            raw_data
                        )
                        VALUES (
                            :tenant_id,
                            :domain,
                            :keyword,
                            :language,
                            :position,
                            :url,
                            :title,
                            :snippet,
                            :engines,
                            :score,
                            :rank_found,
                            :results_checked,
                            :snapshot_date,
                            CAST(:raw_data AS jsonb)
                        )
                        ON CONFLICT (
                            tenant_id,
                            domain,
                            keyword,
                            language,
                            snapshot_date
                        )
                        DO UPDATE SET
                            position = EXCLUDED.position,
                            ranking_url = EXCLUDED.ranking_url,
                            title = EXCLUDED.title,
                            snippet = EXCLUDED.snippet,
                            engines = EXCLUDED.engines,
                            score = EXCLUDED.score,
                            rank_found = EXCLUDED.rank_found,
                            results_checked = EXCLUDED.results_checked,
                            raw_data = EXCLUDED.raw_data
                        """
                    ),
                    {
                        "tenant_id": snapshot.tenant_id,
                        "domain": snapshot.domain,
                        "keyword": snapshot.keyword,
                        "language": snapshot.language,
                        "position": snapshot.position,
                        "url": snapshot.url,
                        "title": snapshot.title,
                        "snippet": snapshot.snippet,
                        "engines": snapshot.engines,
                        "score": snapshot.score,
                        "rank_found": snapshot.rank_found,
                        "results_checked": snapshot.results_checked,
                        "snapshot_date": snapshot.snapshot_date,
                        "raw_data": json.dumps(snapshot.raw_serp_item),
                    },
                )

                inserted += 1

            await session.commit()

        return inserted

    async def _fetch_rank(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        item: dict,
    ) -> RankSnapshot | None:
        """Fetch one keyword SERP and find the target domain."""

        tenant_id = str(item["tenant_id"])
        domain = item["domain"]
        keyword = item["keyword"]
        language = item.get("language") or "en"

        params = {
            "q": keyword,
            "format": "json",
            "language": language,
        }

        try:
            async with sem:
                response = await client.get(
                    f"{self._base_url}/search",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            results = data.get("results") or []
            checked_results = results[:SEARXNG_MAX_RESULTS]

            position = None
            ranking_url = None
            title = None
            snippet = None
            engines: list[str] = []
            score = None

            for index, result in enumerate(
                checked_results,
                start=1,
            ):
                result_url = result.get("url") or ""

                if self._domain_matches(domain, result_url):
                    position = index
                    ranking_url = result_url
                    title = result.get("title")
                    snippet = result.get("content")
                    score = result.get("score")

                    raw_engines = result.get("engines")
                    if isinstance(raw_engines, list):
                        engines = [
                            str(engine)
                            for engine in raw_engines
                        ]
                    elif result.get("engine"):
                        engines = [str(result["engine"])]

                    break

            raw_data = {
                "provider": "searxng",
                "query": keyword,
                "language": language,
                "results_checked": len(checked_results),
                "rank_found": position is not None,
                "matched_result": {
                    "position": position,
                    "title": title,
                    "url": ranking_url,
                    "content": snippet,
                    "engines": engines,
                    "score": score,
                },
                "result_urls": [
                    result.get("url")
                    for result in checked_results
                    if result.get("url")
                ],
            }

            return RankSnapshot(
                tenant_id=tenant_id,
                domain=domain,
                keyword=keyword,
                language=language,
                position=position,
                url=ranking_url,
                title=title,
                snippet=snippet,
                engines=engines,
                score=score,
                rank_found=position is not None,
                results_checked=len(checked_results),
                raw_serp_item=raw_data,
            )

        except Exception as exc:
            logger.warning(
                "rank_fetch_error",
                tenant_id=tenant_id,
                domain=domain,
                keyword=keyword,
                error=str(exc),
            )
            return None

    @staticmethod
    def _domain_matches(domain: str, url: str) -> bool:
        """Safely match a target domain against a result URL."""

        target = domain.strip().lower()

        if target.startswith("www."):
            target = target[4:]

        try:
            hostname = urlparse(url).hostname or ""
        except ValueError:
            return False

        hostname = hostname.lower()

        if hostname.startswith("www."):
            hostname = hostname[4:]

        return (
            hostname == target
            or hostname.endswith("." + target)
        )
