"""
DATA ENGINE — Domain Search Visibility Tracker

Measures how visible a domain is across the tracked search queries
using the internal SearXNG instance.

This replaces the former DataForSEO backlink/authority tracker.

SearXNG can measure search visibility, but it cannot provide:
- Domain Rank
- backlink counts
- referring domains
- dofollow/nofollow counts
- spam scores
- anchor-text metrics

Therefore this module deliberately reports search visibility metrics
instead of pretending to provide backlink authority metrics.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date

import httpx
import structlog

logger = structlog.get_logger(__name__)

SEARXNG_TIMEOUT = 30.0
SEARXNG_MAX_CONCURRENT = 5
SEARXNG_MAX_RESULTS = 20


@dataclass
class VisibilitySnapshot:
    """Search visibility metrics for one tenant/domain on one snapshot date."""

    tenant_id: str
    domain: str
    queries_checked: int
    queries_found: int
    top_3_count: int
    top_10_count: int
    top_20_count: int
    average_position: float | None
    visibility_rate: float | None
    snapshot_date: date = field(default_factory=date.today)
    raw_data: dict = field(default_factory=dict)


@dataclass
class VisibilityBatchResult:
    """Results for a batch domain visibility calculation."""

    snapshots: list[VisibilitySnapshot]
    fetched: int
    errors: int
    search_calls_used: int


class AuthorityTracker:
    """
    Backwards-compatible class name for the former authority tracker.

    Internally this now measures SearXNG search visibility rather than
    DataForSEO domain authority/backlinks.

    Each domain receives a set of tracked search queries. Every query is
    searched through SearXNG and the target domain is checked within the
    first 20 organic results.
    """

    def __init__(self, searxng_url: str = "http://localhost:8080") -> None:
        self._base_url = searxng_url.rstrip("/")

    async def fetch_batch(
        self,
        tenant_domain_queries: dict[str, dict[str, list[str]]],
    ) -> VisibilityBatchResult:
        """
        Calculate search visibility for multiple domains.

        tenant_domain_queries:
            {
                "tenant-uuid": {
                    "example.com": ["query one", "query two"],
                    "competitor.com": ["query one", "query three"],
                }
            }
        """
        if not tenant_domain_queries:
            return VisibilityBatchResult(
                snapshots=[],
                fetched=0,
                errors=0,
                search_calls_used=0,
            )

        sem = asyncio.Semaphore(SEARXNG_MAX_CONCURRENT)

        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            tasks = [
                self._fetch_domain(
                    client=client,
                    sem=sem,
                    tenant_id=tenant_id,
                    domain=domain,
                    queries=queries,
                )
                for tenant_id, domains in tenant_domain_queries.items()
                for domain, queries in domains.items()
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

        snapshots: list[VisibilitySnapshot] = []
        errors = 0
        search_calls = 0

        for result in results:
            if isinstance(result, Exception):
                errors += 1
                logger.warning(
                    "domain_visibility_error",
                    error=str(result),
                )
                continue

            if result is None:
                errors += 1
                continue

            snapshots.append(result)
            search_calls += result.queries_checked

        return VisibilityBatchResult(
            snapshots=snapshots,
            fetched=len(snapshots),
            errors=errors,
            search_calls_used=search_calls,
        )

    async def persist(
        self,
        snapshots: list[VisibilitySnapshot],
    ) -> int:
        """Persist daily domain visibility snapshots."""
        if not snapshots:
            return 0

        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        persisted = 0

        async with AsyncSessionLocal() as session:
            for snapshot in snapshots:
                await session.execute(
                    text(
                        """
                        INSERT INTO domain_visibility (
                            tenant_id,
                            domain,
                            queries_checked,
                            queries_found,
                            top_3_count,
                            top_10_count,
                            top_20_count,
                            average_position,
                            visibility_rate,
                            snapshot_date,
                            raw_data
                        )
                        VALUES (
                            :tenant_id,
                            :domain,
                            :queries_checked,
                            :queries_found,
                            :top_3_count,
                            :top_10_count,
                            :top_20_count,
                            :average_position,
                            :visibility_rate,
                            :snapshot_date,
                            :raw_data::jsonb
                        )
                        ON CONFLICT (
                            tenant_id,
                            domain,
                            snapshot_date
                        )
                        DO UPDATE SET
                            queries_checked = EXCLUDED.queries_checked,
                            queries_found = EXCLUDED.queries_found,
                            top_3_count = EXCLUDED.top_3_count,
                            top_10_count = EXCLUDED.top_10_count,
                            top_20_count = EXCLUDED.top_20_count,
                            average_position = EXCLUDED.average_position,
                            visibility_rate = EXCLUDED.visibility_rate,
                            raw_data = EXCLUDED.raw_data
                        """
                    ),
                    {
                        "tenant_id": snapshot.tenant_id,
                        "domain": snapshot.domain,
                        "queries_checked": snapshot.queries_checked,
                        "queries_found": snapshot.queries_found,
                        "top_3_count": snapshot.top_3_count,
                        "top_10_count": snapshot.top_10_count,
                        "top_20_count": snapshot.top_20_count,
                        "average_position": snapshot.average_position,
                        "visibility_rate": snapshot.visibility_rate,
                        "snapshot_date": snapshot.snapshot_date,
                        "raw_data": __import__("json").dumps(snapshot.raw_data),
                    },
                )
                persisted += 1

            await session.commit()

        return persisted

    async def _fetch_domain(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        tenant_id: str,
        domain: str,
        queries: list[str],
    ) -> VisibilitySnapshot | None:
        """Search every tracked query and aggregate target-domain visibility."""

        if not queries:
            return VisibilitySnapshot(
                tenant_id=tenant_id,
                domain=domain,
                queries_checked=0,
                queries_found=0,
                top_3_count=0,
                top_10_count=0,
                top_20_count=0,
                average_position=None,
                visibility_rate=None,
                raw_data={"provider": "searxng", "queries": []},
            )

        query_results: list[dict] = []
        found_positions: list[int] = []

        for query in queries:
            async with sem:
                try:
                    response = await client.get(
                        f"{self._base_url}/search",
                        params={
                            "q": query,
                            "format": "json",
                            "language": "en",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    logger.warning(
                        "domain_visibility_query_error",
                        domain=domain,
                        query=query,
                        error=str(exc),
                    )
                    continue

            results = payload.get("results") or []

            position = None
            ranking_url = None

            for index, result in enumerate(
                results[:SEARXNG_MAX_RESULTS],
                start=1,
            ):
                result_url = result.get("url") or ""

                if self._domain_matches(domain, result_url):
                    position = index
                    ranking_url = result_url
                    found_positions.append(position)
                    break

            query_results.append(
                {
                    "query": query,
                    "position": position,
                    "url": ranking_url,
                    "results_checked": min(
                        len(results),
                        SEARXNG_MAX_RESULTS,
                    ),
                }
            )

        queries_checked = len(query_results)
        queries_found = len(found_positions)

        average_position = (
            sum(found_positions) / len(found_positions)
            if found_positions
            else None
        )

        visibility_rate = (
            (queries_found / queries_checked) * 100
            if queries_checked
            else None
        )

        return VisibilitySnapshot(
            tenant_id=tenant_id,
            domain=domain,
            queries_checked=queries_checked,
            queries_found=queries_found,
            top_3_count=sum(
                1 for position in found_positions if position <= 3
            ),
            top_10_count=sum(
                1 for position in found_positions if position <= 10
            ),
            top_20_count=sum(
                1 for position in found_positions if position <= 20
            ),
            average_position=average_position,
            visibility_rate=visibility_rate,
            raw_data={
                "provider": "searxng",
                "max_results_checked": SEARXNG_MAX_RESULTS,
                "queries": query_results,
            },
        )

    @staticmethod
    def _domain_matches(domain: str, url: str) -> bool:
        """Safely match a target domain against a result URL."""
        from urllib.parse import urlparse

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

        return hostname == target or hostname.endswith("." + target)
