"""
DATA ENGINE — Dead-Letter Queue & Scraper Health Alerting
Writes failed scraper payloads to the dead-letter queue.
Updates per-platform health state and fires dashboard alerts
when consecutive failures cross the threshold.
"""

from __future__ import annotations

import json
import structlog
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import text
from configs.database import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# Alert threshold: fire dashboard alert after this many consecutive failures
ALERT_THRESHOLD = 3


async def send_to_dead_letter(
    *,
    platform: str,
    source: str,
    query: str | None,
    raw_payload: Any,
    error_type: str,
    error_detail: str,
    api_status_code: int | None = None,
) -> None:
    """
    Persist a failed scraper payload to the dead-letter queue.
    Also updates the scraper_health table for dashboard visibility.

    Args:
        platform:        Platform name e.g. 'twitter', 'tiktok'
        source:          Source category e.g. 'social_media', 'google'
        query:           The search query that triggered the scrape
        raw_payload:     The raw API response (any JSON-serialisable value)
        error_type:      Class name of the error e.g. 'ValidationError'
        error_detail:    Full error message or field path
        api_status_code: HTTP status code if applicable
    """
    try:
        # Safely serialise raw payload — never let bad data block the write
        try:
            serialised = json.dumps(raw_payload, default=str)
        except Exception:
            serialised = json.dumps({"_raw": str(raw_payload)[:5000]})

        async with AsyncSessionLocal() as session:
            # 1. Write to dead-letter queue
            await session.execute(
                text("""
                    INSERT INTO scraper_dead_letter_queue
                        (platform, source, query, raw_payload,
                         error_type, error_detail, api_status_code)
                    VALUES
                        (:platform, :source, :query, :raw_payload::jsonb,
                         :error_type, :error_detail, :api_status_code)
                """),
                {
                    "platform":        platform,
                    "source":          source,
                    "query":           query,
                    "raw_payload":     serialised,
                    "error_type":      error_type,
                    "error_detail":    error_detail[:2000],
                    "api_status_code": api_status_code,
                },
            )

            # 2. Update scraper health row (upsert)
            await session.execute(
                text("""
                    INSERT INTO scraper_health
                        (platform, last_failure_at, consecutive_failures,
                         total_failures, last_error_type, last_error_msg, updated_at)
                    VALUES
                        (:platform, NOW(), 1, 1, :error_type, :error_msg, NOW())
                    ON CONFLICT (platform) DO UPDATE SET
                        last_failure_at      = NOW(),
                        consecutive_failures = scraper_health.consecutive_failures + 1,
                        total_failures       = scraper_health.total_failures + 1,
                        last_error_type      = :error_type,
                        last_error_msg       = :error_msg,
                        updated_at           = NOW()
                """),
                {
                    "platform":   platform,
                    "error_type": error_type,
                    "error_msg":  error_detail[:500],
                },
            )

            # 3. Fire alert if consecutive failures >= threshold
            result = await session.execute(
                text("""
                    SELECT consecutive_failures, alert_fired
                    FROM scraper_health
                    WHERE platform = :platform
                """),
                {"platform": platform},
            )
            row = result.fetchone()
            if row and row.consecutive_failures >= ALERT_THRESHOLD and not row.alert_fired:
                await session.execute(
                    text("""
                        UPDATE scraper_health
                        SET alert_fired = TRUE, alert_fired_at = NOW()
                        WHERE platform = :platform
                    """),
                    {"platform": platform},
                )
                logger.error(
                    "SCRAPER_ALERT_FIRED",
                    platform=platform,
                    consecutive_failures=row.consecutive_failures,
                    last_error=error_detail[:200],
                    action="Check dead-letter queue and platform API changelog",
                )

            await session.commit()

        logger.warning(
            "payload_sent_to_dead_letter_queue",
            platform=platform,
            error_type=error_type,
        )

    except Exception as exc:
        # Dead-letter writer must NEVER crash the worker pipeline
        logger.error("dead_letter_write_failed", platform=platform, error=str(exc))


async def record_scraper_success(platform: str) -> None:
    """
    Reset consecutive failure count and clear alert on successful scrape.
    Call this after each successful platform fetch.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO scraper_health
                        (platform, last_success_at, consecutive_failures,
                         total_successes, alert_fired, updated_at)
                    VALUES
                        (:platform, NOW(), 0, 1, FALSE, NOW())
                    ON CONFLICT (platform) DO UPDATE SET
                        last_success_at      = NOW(),
                        consecutive_failures = 0,
                        total_successes      = scraper_health.total_successes + 1,
                        alert_fired          = FALSE,
                        updated_at           = NOW()
                """),
                {"platform": platform},
            )
            await session.commit()
    except Exception as exc:
        logger.error("health_success_record_failed", platform=platform, error=str(exc))


async def get_scraper_health_summary() -> list[dict]:
    """
    Return health summary for all platforms.
    Used by the dashboard backend to display scraper status.
    """
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT
                        platform,
                        last_success_at,
                        last_failure_at,
                        consecutive_failures,
                        total_failures,
                        total_successes,
                        last_error_type,
                        alert_fired,
                        alert_fired_at,
                        updated_at
                    FROM scraper_health
                    ORDER BY platform
                """)
            )
            rows = result.fetchall()
            return [dict(row._mapping) for row in rows]
    except Exception as exc:
        logger.error("health_summary_failed", error=str(exc))
        return []
