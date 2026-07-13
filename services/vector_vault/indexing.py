"""
DATA ENGINE — Vector Vault: Index Management
HNSW index creation, rebuild, and statistics for PGVector.
Phase 4: Production index management on Node 2 (memory-optimised DB).
"""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import get_settings
from shared.exceptions.base import DatabaseError

logger = structlog.get_logger(__name__)
settings = get_settings()


class VectorIndexManager:
    """Manages HNSW index lifecycle for the embeddings table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_hnsw_index(
        self,
        table: str = "embeddings",
        column: str = "embedding",
        m: int | None = None,
        ef_construction: int | None = None,
    ) -> None:
        """
        Create (or recreate) the HNSW index on the embeddings column.
        Safe to run on a populated table — PGVector builds the index in place.
        """
        m_val = m or settings.hnsw_m
        ef_val = ef_construction or settings.hnsw_ef_construction
        index_name = f"idx_{table}_hnsw"

        try:
            # Drop existing index if present
            await self._session.execute(
                text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
            )
            # Build new HNSW index
            await self._session.execute(
                text(f"""
                    CREATE INDEX CONCURRENTLY {index_name}
                    ON {table} USING hnsw ({column} vector_cosine_ops)
                    WITH (m = {m_val}, ef_construction = {ef_val})
                """)
            )
            logger.info(
                "hnsw_index_created",
                table=table,
                m=m_val,
                ef_construction=ef_val,
            )
        except Exception as exc:
            logger.error("hnsw_index_creation_failed", error=str(exc))
            raise DatabaseError(f"Failed to create HNSW index: {exc}") from exc

    async def get_index_stats(self) -> dict:
        """Return index usage statistics from pg_stat_user_indexes."""
        result = await self._session.execute(
            text("""
                SELECT
                    indexname,
                    idx_scan,
                    idx_tup_read,
                    idx_tup_fetch,
                    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
                FROM pg_stat_user_indexes
                WHERE indexname LIKE '%hnsw%'
                ORDER BY idx_scan DESC
            """)
        )
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def get_vector_table_stats(self) -> dict:
        """Return row count and table size for the embeddings table."""
        result = await self._session.execute(
            text("""
                SELECT
                    COUNT(*) AS row_count,
                    pg_size_pretty(pg_total_relation_size('embeddings')) AS total_size,
                    pg_size_pretty(pg_table_size('embeddings')) AS table_size,
                    pg_size_pretty(pg_indexes_size('embeddings')) AS indexes_size
                FROM embeddings
            """)
        )
        row = result.fetchone()
        return dict(row._mapping) if row else {}

    async def vacuum_analyze(self, table: str = "embeddings") -> None:
        """Run VACUUM ANALYZE on the target table to update planner statistics."""
        try:
            # VACUUM cannot run inside a transaction, use raw connection
            conn = await self._session.connection()
            await conn.execute(text(f"VACUUM ANALYZE {table}"))
            logger.info("vacuum_analyze_complete", table=table)
        except Exception as exc:
            logger.warning("vacuum_analyze_failed", error=str(exc))
