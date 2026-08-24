"""
DATA ENGINE — Domain Authority & Backlink Analyser
Fetches domain authority scores and backlink metrics from DataForSEO's
Backlinks API and persists daily snapshots to the domain_authority table.

DataForSEO Backlinks docs:
  https://docs.dataforseo.com/v3/backlinks/domain_pages/

Metrics returned per domain:
  - Domain Rank (0–100, DataForSEO proprietary metric)
  - Total backlinks count
  - Referring domains count
  - DoFollow vs NoFollow breakdown
  - Spam score (0–1)
  - Top anchor texts
  - New / lost backlinks (last 30 days)

Usage:
    tracker = AuthorityTracker(api_login=..., api_password=...)
    result  = await tracker.fetch_batch(["example.com", "competitor.com"])
    await tracker.persist(result.snapshots)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date

import httpx
import structlog

logger = structlog.get_logger(__name__)

DATAFORSEO_BACKLINKS_SUMMARY_URL = (
    "https://api.dataforseo.com/v3/backlinks/summary/live"
)
DATAFORSEO_TIMEOUT        = 30.0
DATAFORSEO_MAX_CONCURRENT = 5


@dataclass
class AuthoritySnapshot:
    """Domain authority metrics for a single domain at a point in time."""
    domain: str
    domain_rank: int | None         # 0–100, DataForSEO Domain Rank
    total_backlinks: int | None
    referring_domains: int | None
    dofollow_backlinks: int | None
    nofollow_backlinks: int | None
    spam_score: float | None        # 0.0–1.0
    new_backlinks_30d: int | None
    lost_backlinks_30d: int | None
    top_anchors: list[str] = field(default_factory=list)
    snapshot_date: date = field(default_factory=date.today)
    raw_data: dict = field(default_factory=dict)


@dataclass
class AuthorityBatchResult:
    """Results for a batch authority fetch."""
    snapshots: list[AuthoritySnapshot]
    fetched: int
    errors: int
    api_calls_used: int


class AuthorityTracker:
    """
    Fetches domain authority and backlink metrics from DataForSEO
    and persists daily snapshots.

    Supports:
    - Batch domain authority lookups (up to 100 domains per run)
    - Daily snapshots with historical trend storage
    - Graceful degradation if API key is missing
    """

    def __init__(self, api_login: str = "", api_password: str = "") -> None:
        self._login    = api_login
        self._password = api_password
        self._enabled  = bool(api_login and api_password)
        if not self._enabled:
            logger.warning(
                "authority_tracker_disabled",
                reason="DATAFORSEO_LOGIN or DATAFORSEO_PASSWORD not set",
            )

    # ── Public API ───────────────────────────────────────────────────────────

    async def fetch_batch(self, domains: list[str]) -> AuthorityBatchResult:
        """
        Fetch authority metrics for a list of domains.

        Args:
            domains: Domain strings, e.g. ["example.com", "competitor.com"]

        Returns:
            AuthorityBatchResult with per-domain snapshots.
        """
        if not self._enabled or not domains:
            return AuthorityBatchResult(
                snapshots=[], fetched=0, errors=0, api_calls_used=0
            )

        sem = asyncio.Semaphore(DATAFORSEO_MAX_CONCURRENT)
        async with httpx.AsyncClient(
            auth=(self._login, self._password),
            timeout=DATAFORSEO_TIMEOUT,
        ) as client:
            tasks = [self._fetch_domain(client, sem, domain) for domain in domains]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        snapshots: list[AuthoritySnapshot] = []
        errors = 0
        for result in raw_results:
            if isinstance(result, Exception):
                errors += 1
                logger.warning("authority_fetch_failed", error=str(result))
            elif result is not None:
                snapshots.append(result)

        logger.info(
            "authority_batch_complete",
            total=len(domains),
            fetched=len(snapshots),
            errors=errors,
        )

        return AuthorityBatchResult(
            snapshots=snapshots,
            fetched=len(snapshots),
            errors=errors,
            api_calls_used=len(domains) - errors,
        )

    async def persist(self, snapshots: list[AuthoritySnapshot]) -> int:
        """
        Persist authority snapshots to the domain_authority table.

        Args:
            snapshots: List of AuthoritySnapshot objects.

        Returns:
            Number of rows upserted.
        """
        if not snapshots:
            return 0

        import json
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        inserted = 0
        async with AsyncSessionLocal() as session:
            for snap in snapshots:
                await session.execute(
                    text("""
                        INSERT INTO domain_authority
                            (domain, domain_rank, total_backlinks, referring_domains,
                             dofollow_backlinks, nofollow_backlinks, spam_score,
                             new_backlinks_30d, lost_backlinks_30d,
                             top_anchors, snapshot_date, raw_data)
                        VALUES
                            (:domain, :dr, :tbl, :rd, :df, :nf, :spam,
                             :new_bl, :lost_bl, :anchors::text[], :snap_date, :raw::jsonb)
                        ON CONFLICT (domain, snapshot_date)
                        DO UPDATE SET
                            domain_rank         = EXCLUDED.domain_rank,
                            total_backlinks     = EXCLUDED.total_backlinks,
                            referring_domains   = EXCLUDED.referring_domains,
                            dofollow_backlinks  = EXCLUDED.dofollow_backlinks,
                            nofollow_backlinks  = EXCLUDED.nofollow_backlinks,
                            spam_score          = EXCLUDED.spam_score,
                            new_backlinks_30d   = EXCLUDED.new_backlinks_30d,
                            lost_backlinks_30d  = EXCLUDED.lost_backlinks_30d,
                            top_anchors         = EXCLUDED.top_anchors,
                            raw_data            = EXCLUDED.raw_data
                    """),
                    {
                        "domain":    snap.domain,
                        "dr":        snap.domain_rank,
                        "tbl":       snap.total_backlinks,
                        "rd":        snap.referring_domains,
                        "df":        snap.dofollow_backlinks,
                        "nf":        snap.nofollow_backlinks,
                        "spam":      snap.spam_score,
                        "new_bl":    snap.new_backlinks_30d,
                        "lost_bl":   snap.lost_backlinks_30d,
                        "anchors":   "{" + ",".join(
                            f'"{a}"' for a in snap.top_anchors[:20]
                        ) + "}",
                        "snap_date": snap.snapshot_date.isoformat(),
                        "raw":       json.dumps(snap.raw_data),
                    },
                )
                inserted += 1
            await session.commit()

        logger.info("authority_snapshots_persisted", count=inserted)
        return inserted

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _fetch_domain(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        domain: str,
    ) -> AuthoritySnapshot | None:
        """Fetch authority metrics for one domain."""
        payload = [{"target": domain, "include_subdomains": True}]

        try:
            async with sem:
                resp = await client.post(
                    DATAFORSEO_BACKLINKS_SUMMARY_URL, json=payload
                )
                resp.raise_for_status()
                data = resp.json()

            result = (
                data.get("tasks", [{}])[0]
                    .get("result", [{}])[0]
            )
            if not result:
                return None

            anchors = [
                item.get("anchor", "")
                for item in result.get("top_anchors", {}).get("anchors", [])[:20]
            ]

            return AuthoritySnapshot(
                domain=domain,
                domain_rank=result.get("domain_rank"),
                total_backlinks=result.get("total_count"),
                referring_domains=result.get("referring_domains"),
                dofollow_backlinks=result.get("follow"),
                nofollow_backlinks=result.get("nofollow"),
                spam_score=result.get("spam_score"),
                new_backlinks_30d=result.get("new_backlinks"),
                lost_backlinks_30d=result.get("lost_backlinks"),
                top_anchors=anchors,
                raw_data=result,
            )

        except Exception as exc:
            logger.warning("authority_fetch_error", domain=domain, error=str(exc))
            return None
