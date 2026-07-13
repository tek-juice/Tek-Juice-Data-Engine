"""
DATA ENGINE — Ingestion Router
Handles document upload, status queries, and deletion.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, Form, UploadFile, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from configs.constants import DocumentStatus
from shared.authentication.jwt_handler import CurrentUser
from shared.validators.file_validator import validate_upload
from shared.utils.pagination import PaginationParams, PaginatedResponse
from services.ingestion_service.upload_handlers.document_handler import (
    create_document_record,
    dispatch_processing_pipeline,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["Ingestion"])
ingest_router = router


@router.post("/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    source_type: str = Form(default=None),
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Upload a document for processing.
    Validates the file, creates a document record, and dispatches async pipeline.
    """
    content, detected_source_type = await validate_upload(file)
    final_source_type = source_type or detected_source_type.value

    document = await create_document_record(
        db=db,
        tenant_id=current_user.tenant_id,
        filename=file.filename or "unknown",
        source_type=final_source_type,
        content=content,
        mime_type=file.content_type,
    )

    await dispatch_processing_pipeline(document_id=str(document.id), tenant_id=current_user.tenant_id)

    logger.info("document_uploaded", document_id=str(document.id), filename=file.filename)
    return {
        "document_id": str(document.id),
        "status": document.status,
        "filename": document.filename,
        "message": "Document queued for processing.",
    }


@router.get("/status/{document_id}")
async def get_document_status(
    document_id: uuid.UUID,
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Get the processing status of a document."""
    result = await db.execute(
        text("""
            SELECT id, filename, status, chunk_count, error_message, created_at, updated_at
            FROM documents
            WHERE id = :doc_id AND tenant_id = :tenant_id
        """),
        {"doc_id": str(document_id), "tenant_id": current_user.tenant_id},
    )
    row = result.fetchone()
    if not row:
        from shared.exceptions.base import DocumentNotFoundError
        raise DocumentNotFoundError()

    return {
        "document_id": str(row.id),
        "filename": row.filename,
        "status": row.status,
        "chunk_count": row.chunk_count,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/documents")
async def list_documents(
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
):
    """List all documents for the current tenant with pagination."""
    params = PaginationParams(page=page, page_size=page_size)

    where_clause = "WHERE tenant_id = :tenant_id"
    query_params: dict = {"tenant_id": current_user.tenant_id}

    if status:
        where_clause += " AND status = :status"
        query_params["status"] = status

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM documents {where_clause}"), query_params
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        text(f"""
            SELECT id, filename, source_type, status, chunk_count, created_at
            FROM documents {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**query_params, "limit": params.limit, "offset": params.offset},
    )
    items = [dict(row._mapping) for row in result.fetchall()]

    return PaginatedResponse.build(items=items, total=total, params=params)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUser = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Soft-delete a document (marks as deleted, cascades to chunks/embeddings)."""
    await db.execute(
        text("""
            UPDATE documents SET status = :status
            WHERE id = :doc_id AND tenant_id = :tenant_id
        """),
        {
            "status": DocumentStatus.DELETED,
            "doc_id": str(document_id),
            "tenant_id": current_user.tenant_id,
        },
    )
    logger.info("document_deleted", document_id=str(document_id))
