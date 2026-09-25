"""
DATA ENGINE — Celery Ingestion Tasks
Async processing pipeline: preprocess → chunk → embed → store vectors.
These tasks are chained sequentially per document via Celery Canvas.
"""

import structlog
from urllib.parse import urlparse
from celery import shared_task
from configs.constants import DocumentStatus

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.preprocess_document",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def preprocess_document(self, document_id: str, tenant_id: str) -> dict:
    """
    Task 1/4: Extract and clean raw text from the stored file.
    Updates document status to PREPROCESSING.
    """
    import asyncio
    from configs.database import AsyncSessionLocal
    from sqlalchemy import text

    async def _run():
        async with AsyncSessionLocal() as session:
            # Fetch document
            result = await session.execute(
                text("SELECT storage_path, source_type FROM documents WHERE id = :id"),
                {"id": document_id},
            )
            row = result.fetchone()
            if not row:
                raise ValueError(f"Document {document_id} not found")

            # Update status
            await session.execute(
                text("UPDATE documents SET status = :status WHERE id = :id"),
                {"status": DocumentStatus.PREPROCESSING, "id": document_id},
            )

            # Extract text
            from services.ingestion_service.preprocessors.text_extractor import extract_text
            with open(row.storage_path, "rb") as f:
                content = f.read()
            raw_text = extract_text(content, row.source_type)

            # Store raw text back
            await session.execute(
                text("UPDATE documents SET raw_text = :text WHERE id = :id"),
                {"text": raw_text[:5_000_000], "id": document_id},
            )
            await session.commit()
            logger.info("preprocess_complete", document_id=document_id, chars=len(raw_text))
            return {"document_id": document_id, "tenant_id": tenant_id, "char_count": len(raw_text)}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("preprocess_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.chunk_document",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def chunk_document(self, document_id: str, tenant_id: str) -> dict:
    """
    Task 2/4: Split preprocessed text into token-window chunks.
    Persists chunks to the database and updates status to CHUNKING.
    """
    import asyncio
    import uuid
    from configs.database import AsyncSessionLocal
    from sqlalchemy import text
    from services.chunking_service.token_chunker import TokenChunker

    async def _run():
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT raw_text FROM documents WHERE id = :id"),
                {"id": document_id},
            )
            row = result.fetchone()
            if not row or not row.raw_text:
                raise ValueError(f"No raw text for document {document_id}")

            await session.execute(
                text("UPDATE documents SET status = :s WHERE id = :id"),
                {"s": DocumentStatus.CHUNKING, "id": document_id},
            )

            chunker = TokenChunker()
            chunks = chunker.chunk(row.raw_text)

            for chunk in chunks:
                chunk_id = str(uuid.uuid4())
                await session.execute(
                    text("""
                        INSERT INTO chunks
                            (id, document_id, tenant_id, chunk_index, text,
                             token_count, char_start, char_end, strategy)
                        VALUES
                            (:id, :doc_id, :tenant_id, :idx, :text,
                             :tokens, :start, :end, 'token')
                    """),
                    {
                        "id": chunk_id, "doc_id": document_id,
                        "tenant_id": tenant_id, "idx": chunk.chunk_index,
                        "text": chunk.text, "tokens": chunk.token_count,
                        "start": chunk.char_start, "end": chunk.char_end,
                    },
                )

            await session.execute(
                text("UPDATE documents SET chunk_count = :n WHERE id = :id"),
                {"n": len(chunks), "id": document_id},
            )
            await session.commit()
            logger.info("chunking_complete", document_id=document_id, chunks=len(chunks))
            return {"document_id": document_id, "tenant_id": tenant_id, "chunk_count": len(chunks)}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("chunking_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


# ── Automated Website Crawl & Ingest ─────────────────────────────────────────

async def _analyse_page(document_id: str, tenant_id: str, content: str) -> None:
    """Fire SEO/GEO/AEO analysis for a freshly ingested page. Best-effort — never blocks ingestion."""
    import httpx

    payload = {"document_id": document_id, "tenant_id": tenant_id, "content": content}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for service, port, path in [
            ("seo_engine", 8012, "/api/v1/seo/analyze"),
            ("geo_engine", 8013, "/api/v1/geo/analyze"),
            ("aeo_engine", 8014, "/api/v1/aeo/analyze"),
        ]:
            try:
                resp = await client.post(f"http://{service}:{port}{path}", json=payload)
                resp.raise_for_status()
                logger.info("page_analysed", service=service, document_id=document_id)
            except Exception as exc:
                logger.warning("page_analysis_failed", service=service, document_id=document_id, error=str(exc))


@shared_task(
    name="tasks.crawl_and_ingest_website",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=1800,
    time_limit=2100,
)
def crawl_and_ingest_website(self, tenant_id: str) -> dict:
    """
    Automatically crawl a tenant's registered website and ingest every page
    through the full pipeline (chunk → embed → gap → write → webhook).

    Called by:
      - tasks.crawl_all_tenant_websites  (daily Celery beat sweep)
      - POST /api/v1/ingest/crawl        (on-demand when tenant registers URL)

    Steps:
      1. Load tenant's website_url from DB.  Skip if not set.
      2. Use SiteCrawler to fetch all pages (respects robots.txt, handles SPAs).
      3. For each page: create a document record and dispatch the existing
         preprocess → chunk → embed → store → notify pipeline.
      4. Update tenants.last_crawled_at so the next batch sweep skips
         recently crawled tenants.
    """
    import asyncio

    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.ingestion_service.crawlers.site_crawler import SiteCrawler
        from services.ingestion_service.upload_handlers.document_handler import (
            create_document_record,
            dispatch_processing_pipeline,
        )

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("""
                    SELECT website_url, crawl_config
                    FROM tenants
                    WHERE id = :tenant_id AND is_active = TRUE
                """),
                {"tenant_id": tenant_id},
            )
            tenant = row.fetchone()

        if not tenant or not tenant.website_url:
            logger.info("crawl_skipped_no_url", tenant_id=tenant_id)
            return {"tenant_id": tenant_id, "skipped": True, "reason": "no_website_url"}

        cfg         = tenant.crawl_config or {}
        max_pages   = int(cfg.get("max_pages",  50))
        max_depth   = int(cfg.get("max_depth",   3))
        website_url = tenant.website_url.rstrip("/")

        logger.info("crawl_started", tenant_id=tenant_id, url=website_url,
                    max_pages=max_pages, max_depth=max_depth)

        crawler  = SiteCrawler(max_pages=max_pages, max_depth=max_depth)
        ingested = 0
        failed   = 0

        seen_urls: set[str] = set()

        async for page in crawler.crawl(website_url):
            if not page.text or len(page.text.strip()) < 100:
                continue  # skip near-empty pages (nav-only, 404s, etc.)

            # Normalise URL for dedup
            page_url_norm = page.url.split("#")[0].rstrip("/")
            if page_url_norm in seen_urls:
                logger.info("page_skipped_duplicate_url", url=page.url)
                continue
            seen_urls.add(page_url_norm)

            try:
                # Encode page text as bytes so create_document_record can store it
                page_content = page.text.encode("utf-8")
                # Use normalised URL path as filename so re-crawls update the same logical page
                url_path  = urlparse(page_url_norm).path.strip("/").replace("/", "_")
                # Root path "/" must not collide with "/home" — use "index" for root
                filename  = (url_path or "index")[:180] + ".html"

                async with AsyncSessionLocal() as session:
                    # Skip if this URL was already ingested for this tenant
                    existing = await session.execute(
                        text("SELECT id FROM documents WHERE tenant_id = :tid AND filename = :fn AND status != 'failed' LIMIT 1"),
                        {"tid": tenant_id, "fn": filename},
                    )
                    if existing.fetchone():
                        logger.info("page_skipped_already_ingested", tenant_id=tenant_id, url=page.url)
                        ingested += 1  # count as done
                        continue

                    doc = await create_document_record(
                        db=session,
                        tenant_id=tenant_id,
                        filename=filename,
                        source_type="html",
                        content=page_content,
                        mime_type="text/html",
                    )
                    await session.commit()  # must commit before closing so the row is visible to workers
                    doc_id = str(doc.id)

                await dispatch_processing_pipeline(
                    document_id=doc_id,
                    tenant_id=tenant_id,
                )
                ingested += 1
                logger.info("page_ingested", tenant_id=tenant_id, url=page.url, doc_id=doc_id)

                # Trigger SEO/GEO/AEO analysis on the freshly ingested page content
                await _analyse_page(doc_id, tenant_id, page.text)

            except Exception as exc:
                failed += 1
                logger.warning("page_ingest_failed", tenant_id=tenant_id,
                               url=getattr(page, "url", "?"), error=str(exc))

        # Stamp last_crawled_at so the daily batch skips this tenant until tomorrow
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE tenants SET last_crawled_at = NOW() WHERE id = :tid"),
                {"tid": tenant_id},
            )
            await session.commit()

        logger.info("crawl_complete", tenant_id=tenant_id,
                    url=website_url, ingested=ingested, failed=failed)
        return {
            "tenant_id": tenant_id,
            "website_url": website_url,
            "pages_ingested": ingested,
            "pages_failed": failed,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("crawl_and_ingest_failed", tenant_id=tenant_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.crawl_all_tenant_websites", bind=True)
def crawl_all_tenant_websites(self) -> dict:
    """
    Daily batch sweep: dispatch crawl_and_ingest_website for every active
    tenant that has a website_url registered and hasn't been crawled in
    the last recrawl_interval_hours (default 24h).

    This is the heartbeat that makes the Engine fully autonomous —
    every registered tenant's website is kept perpetually up-to-date
    with zero human effort after initial URL registration.
    """
    import asyncio

    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT id
                    FROM tenants
                    WHERE is_active    = TRUE
                      AND website_url IS NOT NULL
                      AND (
                            last_crawled_at IS NULL
                            OR last_crawled_at < NOW() - INTERVAL '1 hour' *
                               COALESCE((crawl_config->>'recrawl_interval_hours')::int, 24)
                          )
                    ORDER BY COALESCE(last_crawled_at, '1970-01-01'::timestamptz) ASC
                """)
            )
            rows = result.fetchall()

        for row in rows:
            crawl_and_ingest_website.delay(str(row.id))

        logger.info("crawl_all_tenants_dispatched", count=len(rows))
        return {"dispatched": len(rows)}

    return asyncio.run(_run())
