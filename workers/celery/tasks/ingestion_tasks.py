"""
DATA ENGINE — Celery Ingestion Tasks
Async processing pipeline: preprocess → chunk → embed → store vectors.
These tasks are chained sequentially per document via Celery Canvas.
"""

import structlog
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
