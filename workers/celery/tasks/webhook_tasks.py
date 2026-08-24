"""
DATA ENGINE — Celery Webhook Delivery Task

tasks.deliver_webhook
  Delivers a single webhook event to all active endpoints registered
  for the tenant + event_type combination.  Called by every pipeline
  stage that produces a product-visible outcome.

  Retry schedule (exponential backoff, 3 attempts max):
    attempt 1: immediate
    attempt 2: 60 s
    attempt 3: 300 s

  A delivery is marked failed only if every registered endpoint for the
  event returned a non-2xx response or a connection error.  Partial
  failures (some endpoints succeed) are logged but not retried.
"""

import asyncio
import structlog
from celery import shared_task
from services.webhooks.webhook_delivery import WebhookDelivery

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.deliver_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=60,
    time_limit=90,
)
def deliver_webhook(
    self,
    tenant_id: str,
    event_type: str,
    payload: dict,
) -> dict:
    """
    Deliver an event webhook to all active product endpoints.

    Args:
        tenant_id:  Tenant UUID — scopes endpoint lookup.
        event_type: Dot-namespaced event string, e.g. "gap.detected".
        payload:    Event data dict (must be JSON-serialisable).

    Returns:
        Summary dict with attempted, succeeded, failed counts.
    """
    async def _run() -> dict:
        from services.webhooks.webhook_delivery import WebhookDelivery

        delivery = WebhookDelivery()
        results  = await delivery.deliver(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
        )

        succeeded = sum(1 for r in results if r.success)
        failed    = sum(1 for r in results if not r.success)

        logger.info(
            "webhook_task_complete",
            tenant_id=tenant_id,
            event_type=event_type,
            attempted=len(results),
            succeeded=succeeded,
            failed=failed,
        )

        # Retry if every endpoint failed (transient network issues)
        if results and failed == len(results):
            raise RuntimeError(
                f"All {failed} webhook endpoint(s) failed for event {event_type!r}"
            )

        return {
            "tenant_id":  tenant_id,
            "event_type": event_type,
            "attempted":  len(results),
            "succeeded":  succeeded,
            "failed":     failed,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "webhook_task_retrying",
            tenant_id=tenant_id,
            event_type=event_type,
            error=str(exc),
            attempt=self.request.retries,
        )
        raise self.retry(
            exc=exc,
            countdown=60 * (2 ** self.request.retries),  # 60s → 120s → 240s
        )


@shared_task(
    name="tasks.notify_document_completed",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def notify_document_completed(self, document_id: str, tenant_id: str) -> dict:
    """
    Called at the end of the ingestion pipeline chain.
    Fires the document.completed webhook so the product knows the
    document has been fully processed and is ready for gap analysis.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from services.webhooks.webhook_events import build_document_completed
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT filename, chunk_count, status FROM documents WHERE id = :id"),
                {"id": document_id},
            )
            row = result.fetchone()

        if not row:
            return {"document_id": document_id, "skipped": True}

        payload = build_document_completed(
            document_id=document_id,
            tenant_id=tenant_id,
            filename=row.filename,
            chunk_count=row.chunk_count or 0,
            status=row.status,
        )
        delivery = WebhookDelivery()
        results  = await delivery.deliver(
            tenant_id=tenant_id,
            event_type="document.completed",
            payload=payload,
        )
        return {
            "document_id": document_id,
            "attempted":   len(results),
            "succeeded":   sum(1 for r in results if r.success),
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("notify_document_completed_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)
