"""
DATA ENGINE — Ingestion Service Pydantic Schemas
Request/response models for the ingestion API.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    filename: str
    message: str


class DocumentStatusResponse(BaseModel):
    document_id: uuid.UUID
    filename: str
    status: str
    chunk_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentListItem(BaseModel):
    id: uuid.UUID
    filename: str
    source_type: str
    status: str
    chunk_count: int
    created_at: datetime


class IngestTextRequest(BaseModel):
    """Ingest raw text directly (no file upload)."""
    text: str = Field(..., min_length=10, description="Raw text content to ingest")
    title: str = Field(..., min_length=1, max_length=500)
    source_type: str = Field(default="txt")
    metadata: dict[str, Any] = Field(default_factory=dict)
