"""
DATA ENGINE — Vector Vault: PGVector Store
Persists and retrieves embeddings from PostgreSQL + PGVector.
Phase 1: Local vector persistence layer.
Phase 4: Production vector vault with HNSW indexing and RLS.
"""

import json
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import get_settings
from configs.security import set_tenant_context_sql
from services.embedding_service.vector_builder import VectorRecord
from shared.exceptions.base import DatabaseError

logger = structlog.get_logger(__name__)
settings = get_settings()


class PGVectorStore:
    """
    Async PGVector persistence layer.
    All operations are scoped to the current tenant via RLS.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store_vectors(self, records: list[VectorRecord]) -> int:
        """
        Bulk-insert VectorRecord objects into the embeddings table.

        Args:
            records: List of VectorRecord objects from the VectorBuilder.

        Returns:
            Number of records inserted.

        Raises:
            DatabaseError: On any database failure.
        """
        if not records:
            return 0

        try:
            # Set RLS tenant context
            tenant_id = records[0].tenant_id
            await self._session.execute(
                text(set_tenant_context_sql(tenant_id))
            )

            inserted = 0
            for record in records:
                # asyncpg does not support :param::type cast syntax.
                # Embed the vector literal directly in the SQL string so
                # pgvector receives it as a properly typed value.
                embedding_str = str(record.embedding)
                await self._session.execute(
                    text(f"""
                        INSERT INTO embeddings
                            (chunk_id, document_id, tenant_id, embedding,
                             provider, model, dimensions, metadata)
                        VALUES
                            (:chunk_id, :document_id, :tenant_id,
                             '{embedding_str}'::vector, :provider, :model,
                             :dimensions, :metadata)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            embedding  = EXCLUDED.embedding,
                            provider   = EXCLUDED.provider,
                            model      = EXCLUDED.model,
                            updated_at = NOW()
                    """),
                    {
                        "chunk_id":    record.chunk_id,
                        "document_id": record.document_id,
                        "tenant_id":   record.tenant_id,
                        "provider":    record.provider,
                        "model":       record.model,
                        "dimensions":  record.dimensions,
                        "metadata":    json.dumps(record.metadata),
                    },
                )
                inserted += 1

            logger.info(
                "vectors_stored",
                document_id=records[0].document_id,
                count=inserted,
            )
            return inserted

        except Exception as exc:
            logger.error("vector_store_failed", error=str(exc))
            raise DatabaseError(f"Failed to store vectors: {exc}") from exc

    async def delete_by_document(self, document_id: str, tenant_id: str) -> int:
        """Delete all embeddings for a document."""
        await self._session.execute(text(set_tenant_context_sql(tenant_id)))
        result = await self._session.execute(
            text("""
                DELETE FROM embeddings
                WHERE document_id = :doc_id AND tenant_id = :tenant_id
            """),
            {"doc_id": document_id, "tenant_id": tenant_id},
        )
        deleted = result.rowcount
        logger.info("vectors_deleted", document_id=document_id, count=deleted)
        return deleted

    async def count_by_document(self, document_id: str, tenant_id: str) -> int:
        """Return the number of embeddings stored for a document."""
        await self._session.execute(text(set_tenant_context_sql(tenant_id)))
        result = await self._session.execute(
            text("""
                SELECT COUNT(*) FROM embeddings
                WHERE document_id = :doc_id AND tenant_id = :tenant_id
            """),
            {"doc_id": document_id, "tenant_id": tenant_id},
        )
        return result.scalar() or 0
