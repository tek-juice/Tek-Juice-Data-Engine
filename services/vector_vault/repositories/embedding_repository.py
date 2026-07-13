"""
DATA ENGINE — Embedding Repository
Higher-level data access layer wrapping the PGVector store and search.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from services.vector_vault.pgvector import PGVectorStore
from services.vector_vault.search import VectorSearch, SearchResult
from services.embedding_service.vector_builder import VectorRecord

logger = structlog.get_logger(__name__)


class EmbeddingRepository:
    """
    Unified repository for all embedding operations.
    Composes PGVectorStore and VectorSearch.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._store = PGVectorStore(session)
        self._search = VectorSearch(session)

    async def save(self, records: list[VectorRecord]) -> int:
        """Persist vector records."""
        return await self._store.store_vectors(records)

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: str,
        top_k: int | None = None,
        threshold: float | None = None,
        document_id: str | None = None,
    ) -> list[SearchResult]:
        """Perform similarity search."""
        return await self._search.search(
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            top_k=top_k,
            similarity_threshold=threshold,
            document_id=document_id,
        )

    async def delete_document_vectors(self, document_id: str, tenant_id: str) -> int:
        """Remove all embeddings for a document."""
        return await self._store.delete_by_document(document_id, tenant_id)

    async def count_document_vectors(self, document_id: str, tenant_id: str) -> int:
        """Count embeddings stored for a document."""
        return await self._store.count_by_document(document_id, tenant_id)
