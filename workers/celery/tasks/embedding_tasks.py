"""
DATA ENGINE — Celery Embedding Tasks
Task 3/4: Generate embeddings for document chunks.
Task 4/4: Store vectors into the Vector Vault (PGVector).
"""

import asyncio
import structlog
from celery import shared_task
from configs.constants import DocumentStatus

logger = structlog.get_logger(__name__)


@shared_task(
    name="tasks.generate_embeddings",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
)
def generate_embeddings(self, document_id: str, tenant_id: str) -> dict:
    """
    Task 3/4: Load all chunks for a document and generate vector embeddings.
    Updates document status to EMBEDDING.
    """
    async def _run():
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.embedding_service.embedding_pipeline import EmbeddingPipeline

        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE documents SET status = :s WHERE id = :id"),
                {"s": DocumentStatus.EMBEDDING, "id": document_id},
            )
            result = await session.execute(
                text("""
                    SELECT id, text, chunk_index, token_count
                    FROM chunks
                    WHERE document_id = :doc_id AND tenant_id = :tenant_id
                    ORDER BY chunk_index
                """),
                {"doc_id": document_id, "tenant_id": tenant_id},
            )
            chunks = [dict(row._mapping) for row in result.fetchall()]

            if not chunks:
                logger.warning("no_chunks_to_embed", document_id=document_id)
                return {"document_id": document_id, "embeddings_generated": 0}

            texts = [c["text"] for c in chunks]
            pipeline = EmbeddingPipeline()
            embeddings, provider, model = await pipeline.embed(texts)

            await session.commit()
            logger.info(
                "embeddings_generated",
                document_id=document_id,
                count=len(embeddings),
                provider=provider,
            )
            return {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "embeddings_generated": len(embeddings),
                "provider": provider,
                "model": model,
                "chunks": [
                    {"chunk_id": str(c["id"]), "text": c["text"],
                     "token_count": c["token_count"], "chunk_index": c["chunk_index"]}
                    for c in chunks
                ],
                "embeddings": embeddings,
            }

    try:
        return asyncio.get_event_loop().run_until_complete(_run())
    except Exception as exc:
        logger.error("embedding_generation_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)


@shared_task(
    name="tasks.store_vectors",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
)
def store_vectors(self, embedding_result: dict) -> dict:
    """
    Task 4/4: Persist generated embeddings into PGVector (Vector Vault).
    Receives the output of generate_embeddings as its input.
    Updates document status to COMPLETED on success.
    """
    document_id = embedding_result.get("document_id", "unknown")

    async def _run():
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        from services.vector_vault.pgvector import PGVectorStore
        from services.embedding_service.vector_builder import VectorRecord

        async with AsyncSessionLocal() as session:
            store = PGVectorStore(session)
            tenant_id = embedding_result["tenant_id"]
            chunks = embedding_result.get("chunks", [])
            embeddings = embedding_result.get("embeddings", [])

            records = [
                VectorRecord(
                    chunk_id=c["chunk_id"],
                    document_id=document_id,
                    tenant_id=tenant_id,
                    text=c["text"],
                    embedding=embeddings[i],
                    provider=embedding_result.get("provider", "openai"),
                    model=embedding_result.get("model", "text-embedding-3-small"),
                    dimensions=len(embeddings[i]),
                    token_count=c.get("token_count", 0),
                    metadata={"chunk_index": c.get("chunk_index", i)},
                )
                for i, c in enumerate(chunks)
            ]

            stored = await store.store_vectors(records)
            await session.execute(
                text("UPDATE documents SET status = :s WHERE id = :id"),
                {"s": DocumentStatus.COMPLETED, "id": document_id},
            )
            await session.commit()
            logger.info("vectors_stored", document_id=document_id, count=stored)
            return {"document_id": document_id, "vectors_stored": stored, "status": "completed"}

    try:
        return asyncio.get_event_loop().run_until_complete(_run())
    except Exception as exc:
        logger.error("vector_store_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)
