"""
DATA ENGINE — Celery Writing Agent Tasks

These tasks wire the LLMWritingAgent into the automated gap-closure pipeline.

Full auto-closure loop (end-to-end):
  scrape_trends  →  embed_scraped_trends  →  run_gap_analysis_batch
    →  auto_close_gaps_batch  →  write_gap_content  →  embed_gap_drafts
    →  auto_close_gaps (re-analysis)  →  gap resolved ✓

tasks.write_gap_content
  Called by GapOptimiser.auto_close_gap() immediately after the close plan
  is persisted. Runs the LLMWritingAgent for the document and persists all
  drafts to gap_content_drafts.

tasks.write_gap_content_batch
  Scheduled sweep (every gap_auto_close_interval_seconds) that dispatches
  write_gap_content for any document whose close action is still 'pending'.

tasks.embed_gap_drafts
  Called after write_gap_content completes. Concatenates all draft texts
  for a document, chunks and embeds them, then stores the vectors so the
  next gap re-analysis sees improved semantic coverage.
"""

import asyncio
import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.write_gap_content",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=600,
    time_limit=900,
)
def write_gap_content(self, document_id: str, tenant_id: str) -> dict:
    """
    Run the LLM Writing Agent for a single document.

    Steps:
      1. Load the close_plan from gap_close_actions.
      2. Instantiate LLMWritingAgent and generate drafts for all clusters.
      3. Persist drafts to gap_content_drafts.
      4. Mark gap_close_actions.status → 'drafts_ready'.
      5. Dispatch tasks.embed_gap_drafts so new content is embedded and
         the next gap analysis measures improved coverage.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from services.gap_detection.writing_agent import LLMWritingAgent
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            # Load the close plan
            result = await session.execute(
                text("""
                    SELECT close_plan, gap_score, severity
                    FROM gap_close_actions
                    WHERE document_id = :doc_id
                      AND tenant_id   = :tenant_id
                    LIMIT 1
                """),
                {"doc_id": document_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()

        if not row or not row.close_plan:
            logger.warning(
                "write_gap_content_no_close_plan",
                document_id=document_id,
            )
            return {"document_id": document_id, "skipped": True, "reason": "no_close_plan"}

        close_plan = row.close_plan if isinstance(row.close_plan, dict) else {}
        if not close_plan.get("clusters"):
            logger.warning(
                "write_gap_content_empty_clusters",
                document_id=document_id,
            )
            return {"document_id": document_id, "skipped": True, "reason": "empty_clusters"}

        # Run the writing agent
        agent  = LLMWritingAgent()
        result_data = await agent.write_drafts(
            document_id=document_id,
            tenant_id=tenant_id,
            close_plan=close_plan,
        )

        # Persist drafts + update close action status
        async with AsyncSessionLocal() as session:
            await agent.persist_drafts(result_data, session)
            await session.commit()

        logger.info(
            "write_gap_content_complete",
            document_id=document_id,
            drafts=result_data.drafts_written,
            failed=result_data.drafts_failed,
            total_words=result_data.total_words_written,
            provider=result_data.provider_used,
            model=result_data.model_used,
        )

        # Dispatch embed task so new content is reflected in vector search
        embed_gap_drafts.delay(document_id, tenant_id)

        # Dispatch injection task — push drafts into the connected product
        from workers.celery.tasks.injection_tasks import inject_drafts_for_tenant
        inject_drafts_for_tenant.apply_async(
            kwargs={"tenant_id": tenant_id},
            countdown=60,   # 60s delay — let embed_gap_drafts finish first
        )

        # ── Fire drafts.ready webhook ─────────────────────────────────────────
        from workers.celery.tasks.webhook_tasks import deliver_webhook
        from services.webhooks.webhook_events import build_drafts_ready
        from configs.settings import get_settings as _get_settings

        _s = _get_settings()
        draft_fetch_url = (
            f"http://{_s.gateway_host}:{_s.gateway_port}"
            f"/api/v1/gaps/drafts/{document_id}?tenant_id={tenant_id}"
        )
        topics_covered = [
            d.topic for d in result_data.drafts
        ]
        webhook_payload = build_drafts_ready(
            document_id=document_id,
            tenant_id=tenant_id,
            drafts_written=result_data.drafts_written,
            total_words=result_data.total_words_written,
            topics_covered=topics_covered,
            model_used=result_data.model_used,
            provider_used=result_data.provider_used,
            draft_fetch_url=draft_fetch_url,
        )
        deliver_webhook.delay(tenant_id, "drafts.ready", webhook_payload)

        return {
            "document_id":   document_id,
            "drafts_written": result_data.drafts_written,
            "drafts_failed":  result_data.drafts_failed,
            "total_words":    result_data.total_words_written,
            "provider":       result_data.provider_used,
            "model":          result_data.model_used,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("write_gap_content_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(name="tasks.write_gap_content_batch", bind=True)
def write_gap_content_batch(self) -> dict:
    """
    Scheduled sweep: dispatch write_gap_content for all documents whose
    close action is still 'pending' (plan created but not yet written).
    Orders by highest gap_score first.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT document_id, tenant_id
                    FROM gap_close_actions
                    WHERE status = 'pending'
                    ORDER BY gap_score DESC
                    LIMIT 50
                """)
            )
            rows = result.fetchall()

        for row in rows:
            write_gap_content.delay(str(row.document_id), str(row.tenant_id))

        logger.info("write_gap_content_batch_dispatched", count=len(rows))
        return {"dispatched": len(rows)}

    return asyncio.run(_run())


@shared_task(
    name="tasks.embed_gap_drafts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def embed_gap_drafts(self, document_id: str, tenant_id: str) -> dict:
    """
    Embed all approved drafts for a document and store them as new vectors
    so the next gap re-analysis finds improved semantic coverage.

    Process:
      1. Load all 'draft' status rows from gap_content_drafts.
      2. Concatenate draft_text fields (one per cluster).
      3. Chunk and embed via the existing EmbeddingPipeline.
      4. Upsert vectors into embeddings table under the source document.
      5. Mark gap_content_drafts status → 'embedded'.
      6. Schedule auto_close_gaps for re-analysis in 5 minutes.
    """
    async def _run() -> dict:
        from configs.database import AsyncSessionLocal
        from services.embedding_service.embedding_pipeline import EmbeddingPipeline
        from services.chunking_service.token_chunker import TokenChunker
        from sqlalchemy import text
        import uuid

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT id, topic, intent, draft_text
                    FROM gap_content_drafts
                    WHERE document_id = :doc_id
                      AND tenant_id   = :tenant_id
                      AND status      = 'draft'
                    ORDER BY priority ASC
                """),
                {"doc_id": document_id, "tenant_id": tenant_id},
            )
            draft_rows = result.fetchall()

        if not draft_rows:
            logger.info("embed_gap_drafts_nothing_to_embed", document_id=document_id)
            return {"document_id": document_id, "embedded": 0}

        # Concatenate all drafts into one body of text for chunking
        full_text = "\n\n".join(
            f"## {row.topic} ({row.intent})\n{row.draft_text}"
            for row in draft_rows
        )

        # Chunk
        chunker = TokenChunker()
        chunks  = chunker.chunk(full_text)

        if not chunks:
            return {"document_id": document_id, "embedded": 0}

        # Embed
        pipeline   = EmbeddingPipeline()
        texts      = [c.text for c in chunks]
        embeddings, provider, model = await pipeline.embed(texts)

        # Store vectors as new chunks + embeddings under the same document
        async with AsyncSessionLocal() as session:
            stored = 0
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = str(uuid.uuid4())
                await session.execute(
                    text("""
                        INSERT INTO chunks
                            (id, document_id, tenant_id, chunk_index, text,
                             token_count, char_start, char_end, strategy)
                        VALUES
                            (:id, :doc_id, :tenant_id, :idx, :text,
                             :tokens, :start, :end, 'gap_draft')
                    """),
                    {
                        "id":        chunk_id,
                        "doc_id":    document_id,
                        "tenant_id": tenant_id,
                        "idx":       10000 + i,        # above existing chunk indices
                        "text":      chunk.text,
                        "tokens":    chunk.token_count,
                        "start":     chunk.char_start,
                        "end":       chunk.char_end,
                    },
                )

                dim    = len(embedding)
                e_col  = f"embedding_{dim}"
                await session.execute(
                    text(f"""
                        INSERT INTO embeddings
                            (id, document_id, chunk_id, tenant_id, {e_col},
                             provider, model, dimensions)
                        VALUES
                            (:id, :doc_id, :chunk_id, :tenant_id,
                             :embedding, :provider, :model, :dim)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id":        str(uuid.uuid4()),
                        "doc_id":    document_id,
                        "chunk_id":  chunk_id,
                        "tenant_id": tenant_id,
                        "embedding": embedding,
                        "provider":  provider,
                        "model":     model,
                        "dim":       dim,
                    },
                )
                stored += 1

            # Mark drafts as embedded
            draft_ids = [str(row.id) for row in draft_rows]
            await session.execute(
                text("""
                    UPDATE gap_content_drafts
                    SET status = 'embedded'
                    WHERE id = ANY(:ids)
                """),
                {"ids": draft_ids},
            )
            await session.commit()

        logger.info(
            "gap_drafts_embedded",
            document_id=document_id,
            chunks=stored,
            provider=provider,
            model=model,
        )

        # Re-run gap analysis now that new vectors exist — verify coverage improved
        from workers.celery.tasks.auto_close_tasks import auto_close_gaps
        auto_close_gaps.apply_async(
            kwargs={"document_id": document_id, "tenant_id": tenant_id},
            countdown=300,   # 5 minutes — let PGVector index settle
        )

        return {
            "document_id": document_id,
            "embedded":    stored,
            "provider":    provider,
            "model":       model,
        }

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error("embed_gap_drafts_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)
