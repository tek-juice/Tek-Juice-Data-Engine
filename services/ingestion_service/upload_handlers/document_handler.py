"""
DATA ENGINE — Document Upload Handler
Creates document DB records and dispatches Celery pipeline tasks.
"""

import uuid
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.constants import DocumentStatus
from configs.settings import get_settings
from shared.utils.hashing import document_fingerprint

settings = get_settings()
logger = structlog.get_logger(__name__)


async def create_document_record(
    db: AsyncSession,
    tenant_id: str,
    filename: str,
    source_type: str,
    content: bytes,
    mime_type: str | None = None,
) -> object:
    """
    Persist a new document record and store raw content to disk.
    Returns the created document row.
    """
    document_id = str(uuid.uuid4())
    storage_path = _save_to_storage(document_id, filename, content)

    await db.execute(
        text("""
            INSERT INTO documents
                (id, tenant_id, filename, source_type, status,
                 file_size_bytes, mime_type, storage_path, metadata)
            VALUES
                (:id, :tenant_id, :filename, :source_type, :status,
                 :file_size_bytes, :mime_type, :storage_path, cast(:metadata as jsonb))
        """),
        {
            "id": document_id,
            "tenant_id": tenant_id,
            "filename": filename,
            "source_type": source_type,
            "status": DocumentStatus.QUEUED,
            "file_size_bytes": len(content),
            "mime_type": mime_type,
            "storage_path": storage_path,
            "metadata": f'{{"fingerprint": "{document_fingerprint(filename, content)}"}}',
        },
    )

    result = await db.execute(
        text("SELECT * FROM documents WHERE id = :id"),
        {"id": document_id},
    )
    return result.fetchone()


def _save_to_storage(document_id: str, filename: str, content: bytes) -> str:
    """Save raw file content to the uploads directory."""
    import os
    upload_dir = os.path.join(settings.storage_base_path, "uploads", document_id)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, filename)
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path


async def dispatch_processing_pipeline(document_id: str, tenant_id: str) -> None:
    """
    Dispatch the full processing pipeline as a Celery chain:
    preprocess → chunk → embed → store → webhook(document.completed)
    """
    try:
        from celery import chain
        from workers.celery.app import celery_app

        pipeline = chain(
            celery_app.signature("tasks.preprocess_document", args=[document_id, tenant_id], immutable=True),
            celery_app.signature("tasks.chunk_document", args=[document_id, tenant_id], immutable=True),
            celery_app.signature("tasks.generate_embeddings", args=[document_id, tenant_id], immutable=True),
            celery_app.signature("tasks.store_vectors"),
            celery_app.signature("tasks.notify_document_completed", kwargs={"document_id": document_id, "tenant_id": tenant_id}, immutable=True),
        )
        pipeline.delay()
        logger.info("pipeline_dispatched", document_id=document_id)
    except Exception as exc:
        logger.error("pipeline_dispatch_failed", document_id=document_id, error=str(exc))
