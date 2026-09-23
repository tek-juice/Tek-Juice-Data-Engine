"""
DATA ENGINE — Vector Vault: Semantic Search
ANN search using HNSW cosine similarity via PGVector.
"""

import json
import structlog
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import get_settings
from configs.security import set_tenant_context_sql
from shared.validators.vector_validator import validate_embedding, validate_search_params
from shared.exceptions.base import DatabaseError

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class SearchResult:
    """A single semantic search result."""
    chunk_id: str
    document_id: str
    text: str
    similarity: float
    provider: str
    model: str
    metadata: dict[str, Any]


class VectorSearch:
    """
    Performs cosine similarity search against the embeddings table.
    Uses PGVector HNSW index for sub-linear query time.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: str,
        top_k: int | None = None,
        similarity_threshold: float | None = None,
        document_id: str | None = None,
        ef_search: int = 100,
    ) -> list[SearchResult]:
        """
        Perform approximate nearest-neighbour cosine similarity search.

        Args:
            query_embedding:      Query vector (must match stored dimensions).
            tenant_id:            Tenant UUID for RLS scoping.
            top_k:                Maximum results to return.
            similarity_threshold: Minimum cosine similarity (0.0–1.0).
            document_id:          Optional filter to a specific document.
            ef_search:            HNSW ef_search quality parameter.

        Returns:
            List of SearchResult objects ordered by descending similarity.
        """
        k = top_k or settings.vector_top_k
        threshold = similarity_threshold or settings.vector_similarity_threshold

        validate_embedding(query_embedding)
        validate_search_params(k, threshold)

        try:
            # Set RLS + HNSW search quality
            await self._session.execute(text(set_tenant_context_sql(tenant_id)))
            await self._session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))

            # asyncpg rejects ::vector cast on named bind params — inline the literal
            vec_str = str(query_embedding).replace(" ", "")
            where_clauses = [
                "e.tenant_id = :tenant_id",
                f"1 - (e.embedding <=> '{vec_str}'::vector) >= :threshold",
            ]
            params: dict[str, Any] = {
                "tenant_id": tenant_id,
                "threshold": threshold,
                "top_k":     k,
            }

            if document_id:
                where_clauses.append("e.document_id = :document_id")
                params["document_id"] = document_id

            where_sql = " AND ".join(where_clauses)

            result = await self._session.execute(
                text(f"""
                    SELECT
                        e.chunk_id,
                        e.document_id,
                        c.text,
                        1 - (e.embedding <=> '{vec_str}'::vector) AS similarity,
                        e.provider,
                        e.model,
                        e.metadata
                    FROM embeddings e
                    JOIN chunks c ON c.id = e.chunk_id
                    WHERE {where_sql}
                    ORDER BY e.embedding <=> '{vec_str}'::vector
                    LIMIT :top_k
                """),
                params,
            )

            rows = result.fetchall()
            results = [
                SearchResult(
                    chunk_id=str(row.chunk_id),
                    document_id=str(row.document_id),
                    text=row.text,
                    similarity=round(float(row.similarity), 6),
                    provider=row.provider,
                    model=row.model,
                    metadata=row.metadata or {},
                )
                for row in rows
            ]

            logger.debug(
                "vector_search_complete",
                results=len(results),
                top_k=k,
                threshold=threshold,
                tenant_id=tenant_id,
            )
            return results

        except Exception as exc:
            logger.error("vector_search_failed", error=str(exc))
            raise DatabaseError(f"Vector search failed: {exc}") from exc
