"""
DATA ENGINE — Document ORM Model
"""

import uuid
from typing import Any

from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from configs.constants import DocumentStatus, SourceType
from shared.models.base import TenantBaseModel


class Document(TenantBaseModel):
    __tablename__ = "documents"

    filename: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=DocumentStatus.QUEUED
    )
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(Text)

    # Relationships
    chunks: Mapped[list["Chunk"]] = relationship(  # noqa: F821
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["Embedding"]] = relationship(  # noqa: F821
        "Embedding", back_populates="document", cascade="all, delete-orphan"
    )

    @property
    def is_completed(self) -> bool:
        return self.status == DocumentStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == DocumentStatus.FAILED
