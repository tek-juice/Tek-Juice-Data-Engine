"""
DATA ENGINE — SERP Rank Tracker
Fetches current organic search rankings for tracked keywords via the
DataForSEO SERP API and persists daily snapshots to the rank_tracking
table.

DataForSEO docs: https://docs.dataforseo.com/v3/serp/google/organic/
One API call returns the top-100 organic results for a keyword + location.
The tracker records the first position at which the target domain appears.

Usage in a Celery task — call RankTracker.track_batch() with a list of
(domain, keyword, location_code) tuples; results are stored to DB.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date

import httpx
import structlog

logger = structlog.get_logger(__name__)

DATAFORSEO_API_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/regular"
DATAFORSEO_TIMEOUT = 30.0
DATAFORSEO_MAX_CONCURRENT = 5


@dataclass
class RankSnapshot:
    """A single keyword ranking snapshot."""
    domain: str
    keyword: str
    location_code: int          # DataForSEO location code, e.g. 2840 = USA
    position: int | None        # 1-indexed; None = not in top-100
    url: str | None             # Ranking URL found in SERP
    search_volume: int | None   # Monthly search volume if returned
    cpc: float | None           # Cost-per-click estimate
    competition: float | None   # Competition index 0.0–1.0
    snapshot_date: date = field(default_factory=date.today)
    raw_serp_item: dict = field(default_factory=dict)


@dataclass
class RankTrackingResult:
    """Results for a batch tracking run."""
    snapshots: list[RankSnapshot]
    tracked: int
    not_ranked: int             # keywords where domain was not in top-100
    errors: int
    api_calls_used: int


class RankTracker:
    """
    Fetches SERP rankings from DataForSEO and persists snapshots.

    Supports:
    - Google organic rankings (top 100)
    - Position tracking per domain + keyword + location
    - Daily snapshots with historical trend storage
    - Graceful degradation if API key is missing
    """

    def __init__(self, api_login: str = "", api_password: str = "") -> None:
        self._login    = api_login
        self._password = api_password
        self._enabled  = bool(api_login and api_password)
        if not self._enabled:
            logger.warning(
                "rank_tracker_disabled",
                reason="DATAFORSEO_LOGIN or DATAFORSEO_PASSWORD not set",
            )

    # ── Public API ───────────────────────────────────────────────────────────

    async def track_batch(
        self,
        items: list[dict],  # [{"domain": ..., "keyword": ..., "location_code": ...}]
    ) -> RankTrackingResult:
        """
        Fetch current rankings for a list of (domain, keyword, location) items.

        Args:
            items: List of dicts with keys: domain, keyword, location_code.

        Returns:
            RankTrackingResult with all snapshots.
        """
        if not self._enabled:
            return RankTrackingResult(
                snapshots=[], tracked=0, not_ranked=0, errors=0, api_calls_used=0
            )

        sem = asyncio.Semaphore(DATAFORSEO_MAX_CONCURRENT)
        async with httpx.AsyncClient(
            auth=(self._login, self._password),
            timeout=DATAFORSEO_TIMEOUT,
        ) as client:
            tasks = [self._fetch_rank(client, sem, item) for item in items]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        snapshots: list[RankSnapshot] = []
        errors = 0
        for result in raw_results:
            if isinstance(result, Exception):
                errors += 1
                logger.warning("rank_fetch_failed", error=str(result))
            elif result is not None:
                snapshots.append(result)

        not_ranked = sum(1 for s in snapshots if s.position is None)

        logger.info(
            "rank_tracking_batch_complete",
            total=len(items),
            ranked=len(snapshots) - not_ranked,
            not_ranked=not_ranked,
            errors=errors,
        )

        return RankTrackingResult(
            snapshots=snapshots,
            tracked=len(snapshots),
            not_ranked=not_ranked,
            errors=errors,
            api_calls_used=len(items) - errors,
        )

    async def persist(self, snapshots: list[RankSnapshot]) -> int:
        """
        Persist rank snapshots to the rank_tracking table.

        Args:
            snapshots: List of RankSnapshot objects.

        Returns:
            Number of rows inserted.
        """
        if not snapshots:
            return 0

        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        inserted = 0
        async with AsyncSessionLocal() as session:
            for snap in snapshots:
                await session.execute(
                    text("""
                        INSERT INTO rank_tracking
                            (domain, keyword, location_code, position, ranking_url,
                             search_volume, cpc, competition, snapshot_date, raw_data)
                        VALUES
                            (:domain, :keyword, :location_code, :position, :url,
                             :sv, :cpc, :comp, :snap_date, :raw)
                        ON CONFLICT (domain, keyword, location_code, snapshot_date)
                        DO UPDATE SET
                            position    = EXCLUDED.position,
                            ranking_url = EXCLUDED.ranking_url,
                            raw_data    = EXCLUDED.raw_data
                    """),
                    {
                        "domain":        snap.domain,
                        "keyword":       snap.keyword,
                        "location_code": snap.location_code,
                        "position":      snap.position,
                        "url":           snap.url,
                        "sv":            snap.search_volume,
                        "cpc":           snap.cpc,
                        "comp":          snap.competition,
                        "snap_date":     snap.snapshot_date.isoformat(),
                        "raw":           __import__("json").dumps(snap.raw_serp_item),
                    },
                )
                inserted += 1
            await session.commit()

        logger.info("rank_snapshots_persisted", count=inserted)
        return inserted

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _fetch_rank(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        item: dict,
    ) -> RankSnapshot | None:
        """Fetch ranking position for one keyword + domain combination."""
        domain        = item["domain"]
        keyword       = item["keyword"]
        location_code = int(item.get("location_code", 2840))  # default USA

        payload = [{
            "keyword":       keyword,
            "location_code": location_code,
            "language_code": "en",
            "device":        "desktop",
            "depth":         100,
        }]

        try:
            async with sem:
                resp = await client.post(DATAFORSEO_API_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()

            tasks_data = (
                data.get("tasks", [{}])[0]
                    .get("result", [{}])[0]
                    .get("items", [])
            )
            sv  = (data.get("tasks", [{}])[0]
                       .get("result", [{}])[0]
                       .get("keyword_data", {})
                       .get("keyword_info", {})
                       .get("search_volume"))
            cpc = (data.get("tasks", [{}])[0]
                       .get("result", [{}])[0]
                       .get("keyword_data", {})
                       .get("keyword_info", {})
                       .get("cpc"))
            comp = (data.get("tasks", [{}])[0]
                        .get("result", [{}])[0]
                        .get("keyword_data", {})
                        .get("keyword_info", {})
                        .get("competition"))

            position: int | None = None
            ranking_url: str | None = None
            raw_item: dict = {}

            for item_data in tasks_data:
                if item_data.get("type") != "organic":
                    continue
                item_url = item_data.get("url", "")
                if self._domain_matches(domain, item_url):
                    position    = item_data.get("rank_absolute")
                    ranking_url = item_url
                    raw_item    = item_data
                    break

            return RankSnapshot(
                domain=domain,
                keyword=keyword,
                location_code=location_code,
                position=position,
                url=ranking_url,
                search_volume=sv,
                cpc=cpc,
                competition=comp,
                raw_serp_item=raw_item,
            )

        except Exception as exc:
            logger.warning(
                "rank_fetch_error",
                domain=domain,
                keyword=keyword,
                error=str(exc),
            )
            return None

    @staticmethod
    def _domain_matches(domain: str, url: str) -> bool:
        """Check whether a SERP result URL belongs to the tracked domain."""
        domain_clean = re.sub(r'^https?://(www\.)?', '', domain).rstrip("/")
        url_clean    = re.sub(r'^https?://(www\.)?', '', url).rstrip("/")
        return url_clean.startswith(domain_clean)
