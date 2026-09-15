"""
DATA ENGINE — Celery Injection Tasks
Pushes AI-generated content drafts directly into connected product backends.

This is the final step of the autonomous pipeline:

  crawl → chunk → embed → gap detect → write drafts → INJECT INTO PRODUCT

tasks.inject_drafts_for_tenant
  Reads all 'embedded' drafts for a tenant, loads their injection config,
  selects the correct injector, and publishes each draft to the product's
  backend. Updates draft status to 'published' on success, 'failed' on error.

tasks.inject_drafts_batch
  Scheduled sweep — runs every gap_auto_close_interval_seconds.
  Dispatches inject_drafts_for_tenant for every tenant whose injection
  bridge is live and has drafts waiting to be published.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.inject_drafts_for_tenant",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=600,
    time_limit=900,
)
def inject_drafts_for_tenant(self, tenant_id: str) -> dict:
    """
    Push all pending drafts for a tenant into their connected product.

    Steps:
      1. Load tenant injection config + encrypted credentials.
      2. Decrypt credentials.
      3. Select the correct injector based on platform_type.
      4. For each 'embedded' draft: call injector.publish / inject.
      5. Mark draft status → 'published' or 'failed'.
      6. Fire webhook: content.published event.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.onboarding.credential_store import decrypt_credentials

        async with AsyncSessionLocal() as session:
            # Load tenant injection details
            tenant_row = await session.execute(
                text("""
                    SELECT name, platform_type, injection_status,
                           injection_config, injection_credentials
                    FROM tenants
                    WHERE id = :tid AND is_active = TRUE
                """),
                {"tid": tenant_id},
            )
            tenant = tenant_row.fetchone()

        if not tenant:
            logger.warning("inject_tenant_not_found", tenant_id=tenant_id)
            return {"tenant_id": tenant_id, "skipped": True, "reason": "tenant_not_found"}

        if tenant.injection_status != "live":
            logger.info("inject_skipped_not_live", tenant_id=tenant_id,
                        status=tenant.injection_status)
            return {"tenant_id": tenant_id, "skipped": True, "reason": "injection_not_live"}

        if not tenant.injection_credentials:
            logger.warning("inject_no_credentials", tenant_id=tenant_id)
            return {"tenant_id": tenant_id, "skipped": True, "reason": "no_credentials"}

        # Decrypt credentials
        try:
            creds = decrypt_credentials(tenant.injection_credentials)
        except Exception as exc:
            logger.error("inject_decrypt_failed", tenant_id=tenant_id, error=str(exc))
            return {"tenant_id": tenant_id, "skipped": True, "reason": "credential_decrypt_error"}

        platform = tenant.platform_type or "unknown"

        # Select injector
        injector = _get_injector(platform, creds)
        if injector is None:
            logger.warning("inject_no_injector", tenant_id=tenant_id, platform=platform)
            return {"tenant_id": tenant_id, "skipped": True, "reason": f"no_injector_for_{platform}"}

        # Load pending drafts (status = 'embedded' means written + embedded, ready to publish)
        async with AsyncSessionLocal() as session:
            drafts_result = await session.execute(
                text("""
                    SELECT d.id, d.document_id, d.topic, d.intent,
                           d.draft_text, d.geo_score, d.aeo_score,
                           d.composite_score, d.quality_score,
                           doc.filename
                    FROM gap_content_drafts d
                    JOIN documents doc ON doc.id = d.document_id
                    WHERE d.tenant_id = :tid
                      AND d.status    = 'embedded'
                    ORDER BY d.quality_score DESC NULLS LAST,
                             d.composite_score DESC NULLS LAST
                    LIMIT 20
                """),
                {"tid": tenant_id},
            )
            drafts = drafts_result.fetchall()

        if not drafts:
            logger.info("inject_no_pending_drafts", tenant_id=tenant_id)
            return {"tenant_id": tenant_id, "published": 0, "reason": "no_pending_drafts"}

        published = 0
        failed    = 0

        async with AsyncSessionLocal() as session:
            for draft in drafts:
                slug  = _make_slug(draft.topic)
                title = draft.topic.strip()
                body  = draft.draft_text or ""

                try:
                    result = await _inject(injector, platform, title, body, slug)

                    if result.get("success"):
                        await session.execute(
                            text("""
                                UPDATE gap_content_drafts
                                SET status       = 'published',
                                    published_at = NOW()
                                WHERE id = :id
                            """),
                            {"id": str(draft.id)},
                        )
                        published += 1
                        logger.info(
                            "draft_published",
                            tenant_id  = tenant_id,
                            topic      = draft.topic,
                            platform   = platform,
                            result     = result,
                        )
                    else:
                        await session.execute(
                            text("""
                                UPDATE gap_content_drafts
                                SET status = 'failed'
                                WHERE id   = :id
                            """),
                            {"id": str(draft.id)},
                        )
                        failed += 1
                        logger.warning(
                            "draft_inject_failed",
                            tenant_id = tenant_id,
                            topic     = draft.topic,
                            error     = result.get("error"),
                        )

                except Exception as exc:
                    failed += 1
                    logger.error(
                        "draft_inject_exception",
                        tenant_id = tenant_id,
                        topic     = draft.topic,
                        error     = str(exc),
                    )

            await session.commit()

        # Fire webhook: content.published
        if published > 0:
            try:
                from workers.celery.tasks.webhook_tasks import deliver_webhook
                deliver_webhook.delay(
                    tenant_id,
                    "content.published",
                    {
                        "tenant_id":  tenant_id,
                        "product":    tenant.name,
                        "platform":   platform,
                        "published":  published,
                        "failed":     failed,
                    },
                )
            except Exception as exc:
                logger.warning("inject_webhook_failed", error=str(exc))

        logger.info(
            "inject_drafts_complete",
            tenant_id = tenant_id,
            platform  = platform,
            published = published,
            failed    = failed,
        )
        return {
            "tenant_id": tenant_id,
            "platform":  platform,
            "published": published,
            "failed":    failed,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("inject_drafts_task_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.install_ssh_bridge",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=360,
)
def install_ssh_bridge(self, tenant_id: str, platform: str, creds: dict) -> dict:
    """
    Background task: complete the SSH receiver installation after the
    HTTP request returns to the browser. Updates injection_status to
    'live' on success or 'failed' on error so /onboard/ping can report it.
    """
    import asyncio
    import json as _json
    from datetime import datetime, UTC

    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.onboarding.injectors.ssh_injector import SSHInjector
        from services.onboarding.credential_store import encrypt_credentials

        injector = SSHInjector(creds)
        result   = await injector.install_receiver()

        async with AsyncSessionLocal() as session:
            if result.get("success"):
                encrypted = encrypt_credentials(creds)
                config = {
                    "platform":     platform,
                    "installed_at": datetime.now(UTC).isoformat(),
                    "details":      {
                        k: v for k, v in result.items()
                        if k != "success" and isinstance(v, (str, int, float, bool, type(None)))
                    },
                }
                await session.execute(
                    text("""
                        UPDATE tenants
                        SET injection_credentials   = :creds,
                            injection_config        = :config::jsonb,
                            injection_status        = 'live',
                            platform_type           = :platform,
                            onboarding_completed_at = NOW()
                        WHERE id = :id
                    """),
                    {
                        "creds":    encrypted,
                        "config":   _json.dumps(config),
                        "platform": platform,
                        "id":       tenant_id,
                    },
                )
                # Fire first crawl
                try:
                    from workers.celery.tasks.ingestion_tasks import crawl_and_ingest_website
                    crawl_and_ingest_website.delay(tenant_id)
                except Exception:
                    pass
            else:
                await session.execute(
                    text("UPDATE tenants SET injection_status='failed' WHERE id=:id"),
                    {"id": tenant_id},
                )
            await session.commit()

        logger.info("ssh_bridge_install_complete", tenant_id=tenant_id,
                    success=result.get("success"), platform=platform)
        return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("ssh_bridge_install_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.inject_drafts_batch", bind=True)
def inject_drafts_batch(self) -> dict:
    """
    Scheduled sweep — dispatch inject_drafts_for_tenant for every tenant
    whose injection bridge is live and has drafts in 'embedded' status
    waiting to be published.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT DISTINCT d.tenant_id
                    FROM gap_content_drafts d
                    JOIN tenants t ON t.id = d.tenant_id
                    WHERE d.status         = 'embedded'
                      AND t.injection_status = 'live'
                      AND t.is_active       = TRUE
                      AND t.injection_credentials IS NOT NULL
                    ORDER BY d.tenant_id
                """)
            )
            rows = result.fetchall()

        for row in rows:
            inject_drafts_for_tenant.delay(str(row.tenant_id))

        logger.info("inject_drafts_batch_dispatched", count=len(rows))
        return {"dispatched": len(rows)}

    return asyncio.run(_run())


# ── Injector factory ──────────────────────────────────────────────────────────

def _get_injector(platform: str, creds: dict):
    """
    Return the correct injector instance for the given platform.
    Returns None if no injector is available for this platform.
    """
    try:
        if platform == "wordpress":
            from services.onboarding.injectors.wordpress import WordPressInjector
            return WordPressInjector(creds)

        if platform == "shopify":
            from services.onboarding.injectors.shopify import ShopifyInjector
            return ShopifyInjector(creds)

        if platform == "wix":
            from services.onboarding.injectors.wix import WixInjector
            return WixInjector(creds)

        if platform == "webflow":
            from services.onboarding.injectors.webflow import WebflowInjector
            return WebflowInjector(creds)

        if platform == "graphql":
            from services.onboarding.injectors.graphql_injector import GraphQLInjector
            return GraphQLInjector(creds)

        if platform in ("ssh_custom", "nextjs", "laravel", "django", "express", "unknown"):
            # SSH-based platforms use an HTTP call to the installed receiver endpoint
            # The receiver URL is stored in injection_config — handled in _inject()
            return {"type": "http_receiver", "creds": creds}

    except Exception as exc:
        logger.error("injector_factory_failed", platform=platform, error=str(exc))

    return None


async def _inject(injector, platform: str, title: str, body: str, slug: str) -> dict:
    """
    Route the publish call to the correct injector method.
    """
    # Platform-specific APIs
    if platform == "wordpress":
        return await injector.publish_post(
            title=title, body=body, slug=slug,
        )

    if platform == "shopify":
        return await injector.publish_article(
            title=title, body=body,
        )

    if platform == "wix":
        return await injector.publish_post(
            title=title, body=body,
        )

    if platform == "webflow":
        return await injector.publish_item(
            title=title, body=body, slug=slug,
        )

    if platform == "graphql":
        return await injector.publish(
            title=title, body=body, slug=slug,
        )

    # SSH-based custom backends — POST to the installed receiver endpoint
    if isinstance(injector, dict) and injector.get("type") == "http_receiver":
        import httpx
        creds    = injector["creds"]
        endpoint = creds.get("receiver_url") or f"http://{creds.get('host', 'localhost')}/data-engine/publish"
        api_key  = creds.get("api_key", "")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    endpoint,
                    json={"title": title, "body": body, "slug": slug},
                    headers={"X-DataEngine-Key": api_key, "Content-Type": "application/json"},
                )
            if r.status_code in (200, 201):
                return {"success": True}
            return {"success": False, "error": f"Receiver returned {r.status_code}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": f"No inject handler for platform: {platform}"}


def _make_slug(topic: str) -> str:
    """Convert a topic string to a URL-safe slug."""
    import re
    slug = topic.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:120]
