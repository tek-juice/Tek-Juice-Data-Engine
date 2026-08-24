"""
DATA ENGINE — Vector Builder
Converts document chunks into embedding records ready for persistence.
Bridges the chunking service output and the embedding pipeline.
"""

import uuid
import structlog
from dataclasses import dataclass
from typing import Any

from configs.settings import get_settings
from services.embedding_service.embedding_pipeline import EmbeddingPipeline
from shared.utils.hashing import content_fingerprint

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class VectorRecord:
    """A single embedding ready for storage in the Vector Vault."""
    chunk_id: str
    document_id: str
    tenant_id: str
    text: str
    embedding: list[float]
    provider: str
    model: str
    dimensions: int
    token_count: int
    metadata: dict[str, Any]


class VectorBuilder:
    """
    Takes chunked text and produces VectorRecord objects.

    Usage:
        builder = VectorBuilder()
        records = await builder.build(chunks, document_id, tenant_id)
    """

    def __init__(self, pipeline: EmbeddingPipeline | None = None) -> None:
        self._pipeline = pipeline or EmbeddingPipeline()

    async def build(
        self,
        chunks: list[dict],
        document_id: str,
        tenant_id: str,
        provider: str | None = None,
    ) -> list[VectorRecord]:
        """
        Embed a list of chunk dicts and return VectorRecord objects.

        Args:
            chunks: List of dicts with keys: chunk_id, text, token_count, chunk_index
            document_id: Parent document UUID string.
            tenant_id: Tenant UUID string.
            provider: Optional override embedding provider.

        Returns:
            List of VectorRecord objects with populated embeddings.
        """
        if not chunks:
            return []

        texts = [c["text"] for c in chunks]
        embeddings, provider_name, model_name = await self._pipeline.embed(
            texts, provider=provider
        )

        if len(embeddings) != len(chunks):
            raise ValueError(
                f"Embedding count mismatch: {len(embeddings)} embeddings for {len(chunks)} chunks"
            )

        records: list[VectorRecord] = []
        for chunk, embedding in zip(chunks, embeddings):
            records.append(
                VectorRecord(
                    chunk_id=chunk.get("chunk_id") or chunk.get("id") or str(uuid.uuid4()),
                    document_id=document_id,
                    tenant_id=tenant_id,
                    text=chunk["text"],
                    embedding=embedding,
                    provider=provider_name,
                    model=model_name,
                    dimensions=len(embedding),
                    token_count=chunk.get("token_count", 0),
                    metadata={
                        "chunk_index": chunk.get("chunk_index", 0),
                        "fingerprint": content_fingerprint(chunk["text"]),
                        "strategy": chunk.get("strategy", "token"),
                    },
                )
            )

        logger.info(
            "vectors_built",
            document_id=document_id,
            records=len(records),
            provider=provider_name,
        )
        return records
