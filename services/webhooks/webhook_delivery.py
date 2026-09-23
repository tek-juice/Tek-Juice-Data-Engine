"""
DATA ENGINE — Webhook Delivery Engine

Delivers signed event payloads to product-registered callback URLs.

Design
──────
  Every tenant registers one or more webhook endpoints via the API.
  When the Engine raises an event (gap detected, drafts ready, schema
  generated, document completed, ranking changed) this module:

    1. Loads all active endpoint rows for the tenant + event type.
    2. Builds a signed payload: HMAC-SHA256 of the JSON body using the
       endpoint's secret.  The product verifies the signature on receipt —
       identical to the Stripe / GitHub webhook pattern.
    3. POSTs the payload with a 15-second timeout.
    4. Persists the attempt result to webhook_delivery_log.
    5. On failure, raises so the Celery task can apply exponential backoff.

Signature header: X-DataEngine-Signature: sha256=<hex_digest>
Timestamp header: X-DataEngine-Timestamp: <unix_epoch_int>

Products verify:
    expected = hmac.new(secret.encode(), body_bytes, sha256).hexdigest()
    assert request.headers["X-DataEngine-Signature"] == f"sha256={expected}"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

import httpx
import structlog

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WebhookEndpoint:
    """A registered product callback URL for one or more event types."""
    id: str
    tenant_id: str
    url: str
    secret: str               # raw secret used for HMAC signing
    event_types: list[str]    # e.g. ["gap.detected", "drafts.ready", "*"]
    is_active: bool = True
    description: str = ""


@dataclass
class DeliveryResult:
    """Outcome of one webhook delivery attempt."""
    endpoint_id: str
    tenant_id: str
    event_type: str
    url: str
    status_code: int | None
    success: bool
    error: str | None = None
    duration_ms: int = 0
    attempted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Core delivery logic ───────────────────────────────────────────────────────

class WebhookDelivery:
    """
    Loads registered endpoints and delivers a signed event payload to each.

    Usage:
        delivery = WebhookDelivery()
        results  = await delivery.deliver(
            tenant_id="uuid",
            event_type="gap.detected",
            payload={"document_id": "...", "gap_score": 0.72, ...},
        )
    """

    async def deliver(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[DeliveryResult]:
        """
        Deliver an event to all active endpoints registered for this tenant
        and event type.

        Args:
            tenant_id:  Tenant UUID.
            event_type: Dot-namespaced event string, e.g. "gap.detected".
            payload:    Event data dict (will be JSON-serialised).

        Returns:
            List of DeliveryResult — one per endpoint attempted.
        """
        endpoints = await self._load_endpoints(tenant_id, event_type)
        if not endpoints:
            return []

        results: list[DeliveryResult] = []
        for endpoint in endpoints:
            result = await self._deliver_one(endpoint, event_type, payload)
            results.append(result)
            await self._log_attempt(result)

        return results

    async def _deliver_one(
        self,
        endpoint: WebhookEndpoint,
        event_type: str,
        payload: dict[str, Any],
    ) -> DeliveryResult:
        """POST the signed payload to one endpoint."""
        timestamp   = int(time.time())
        body        = json.dumps({
            "event":      event_type,
            "timestamp":  timestamp,
            "tenant_id":  endpoint.tenant_id,
            "data":       payload,
        }, separators=(",", ":"))
        body_bytes  = body.encode("utf-8")
        signature   = self._sign(endpoint.secret, body_bytes)

        headers = {
            "Content-Type":              "application/json",
            "X-DataEngine-Signature":    f"sha256={signature}",
            "X-DataEngine-Timestamp":    str(timestamp),
            "X-DataEngine-Event":        event_type,
            "User-Agent":                "DataEngine-Webhook/1.0",
        }

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=settings.webhook_timeout_seconds
            ) as client:
                resp = await client.post(
                    endpoint.url,
                    content=body_bytes,
                    headers=headers,
                )
            duration_ms = int((time.monotonic() - start) * 1000)
            success     = 200 <= resp.status_code < 300

            logger.info(
                "webhook_delivered",
                endpoint_id=endpoint.id,
                url=endpoint.url,
                event=event_type,
                status=resp.status_code,
                duration_ms=duration_ms,
            )
            return DeliveryResult(
                endpoint_id=endpoint.id,
                tenant_id=endpoint.tenant_id,
                event_type=event_type,
                url=endpoint.url,
                status_code=resp.status_code,
                success=success,
                duration_ms=duration_ms,
                error=None if success else f"HTTP {resp.status_code}",
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "webhook_delivery_failed",
                endpoint_id=endpoint.id,
                url=endpoint.url,
                event=event_type,
                error=str(exc),
            )
            return DeliveryResult(
                endpoint_id=endpoint.id,
                tenant_id=endpoint.tenant_id,
                event_type=event_type,
                url=endpoint.url,
                status_code=None,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _load_endpoints(
        self, tenant_id: str, event_type: str
    ) -> list[WebhookEndpoint]:
        """
        Load all active endpoints for this tenant that subscribe to
        `event_type` or the wildcard "*".
        """
        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT id, tenant_id, url, secret, event_types,
                               is_active, description
                        FROM webhook_endpoints
                        WHERE tenant_id = :tenant_id
                          AND is_active  = TRUE
                          AND (
                                event_types @> ARRAY[:event_type]::text[]
                             OR event_types @> ARRAY['*']::text[]
                          )
                    """),
                    {"tenant_id": tenant_id, "event_type": event_type},
                )
                rows = result.fetchall()

            return [
                WebhookEndpoint(
                    id=str(row.id),
                    tenant_id=str(row.tenant_id),
                    url=row.url,
                    secret=row.secret,
                    event_types=list(row.event_types or []),
                    is_active=row.is_active,
                    description=row.description or "",
                )
                for row in rows
            ]
        except Exception as exc:
            logger.error("webhook_endpoint_load_failed", error=str(exc))
            return []

    async def _log_attempt(self, result: DeliveryResult) -> None:
        """Persist the delivery attempt to webhook_delivery_log."""
        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO webhook_delivery_log
                            (endpoint_id, tenant_id, event_type, url,
                             status_code, success, error, duration_ms, attempted_at)
                        VALUES
                            (:endpoint_id, :tenant_id, :event_type, :url,
                             :status_code, :success, :error, :duration_ms, :attempted_at)
                    """),
                    {
                        "endpoint_id":  result.endpoint_id,
                        "tenant_id":    result.tenant_id,
                        "event_type":   result.event_type,
                        "url":          result.url,
                        "status_code":  result.status_code,
                        "success":      result.success,
                        "error":        result.error,
                        "duration_ms":  result.duration_ms,
                        "attempted_at": result.attempted_at,
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.error("webhook_log_failed", error=str(exc))

    @staticmethod
    def _sign(secret: str, body: bytes) -> str:
        """HMAC-SHA256 signature of the body using the endpoint secret."""
        return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
