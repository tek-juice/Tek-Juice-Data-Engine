"""
DATA ENGINE — Data Replication
Handles cross-node data consistency for Phase 4 production deployment.
Replicates critical records between compute node and DB node.
"""

import structlog
from dataclasses import dataclass

logger = structlog.get_logger(__name__)


@dataclass
class ReplicationStatus:
    """Status of a replication operation."""
    source: str
    target: str
    table: str
    records_replicated: int
    lag_seconds: float
    success: bool
    error: str | None = None


class DataReplicator:
    """
    Manages data replication strategy between nodes.
    In Phase 4, the primary DB runs on Node 2 (memory-optimised).
    Compute Node 1 maintains a hot read replica for low-latency queries.

    For PostgreSQL streaming replication: configure in postgresql.conf.
    This class handles application-level replication for caches and
    materialised views.
    """

    async def replicate_hot_cache(
        self,
        table: str,
        tenant_id: str | None = None,
        limit: int = 1000,
    ) -> ReplicationStatus:
        """
        Replicate recent records from primary DB to Redis cache layer.
        Used for frequently-accessed embeddings and search indexes.
        """
        try:
            from configs.database import AsyncSessionLocal
            from configs.settings import get_settings
            import redis.asyncio as aioredis
            import json
            from sqlalchemy import text

            settings = get_settings()
            count = 0

            async with AsyncSessionLocal() as session:
                where = "WHERE tenant_id = :tenant_id" if tenant_id else ""
                params = {"tenant_id": tenant_id, "limit": limit} if tenant_id else {"limit": limit}

                result = await session.execute(
                    text(f"""
                        SELECT id, tenant_id, metadata, updated_at
                        FROM {table}
                        {where}
                        ORDER BY updated_at DESC
                        LIMIT :limit
                    """),
                    params,
                )
                rows = result.fetchall()

                redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
                pipe = redis.pipeline()
                for row in rows:
                    cache_key = f"replica:{table}:{row.id}"
                    pipe.setex(cache_key, 3600, json.dumps({"id": str(row.id), "metadata": row.metadata}))
                    count += 1
                await pipe.execute()
                await redis.aclose()

            return ReplicationStatus(
                source="postgresql",
                target="redis",
                table=table,
                records_replicated=count,
                lag_seconds=0.0,
                success=True,
            )

        except Exception as exc:
            logger.error("replication_failed", table=table, error=str(exc))
            return ReplicationStatus(
                source="postgresql",
                target="redis",
                table=table,
                records_replicated=0,
                lag_seconds=0.0,
                success=False,
                error=str(exc),
            )

    async def get_replication_lag(self) -> dict:
        """
        Query PostgreSQL streaming replication lag.
        Returns lag in seconds from pg_stat_replication.
        """
        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            client_addr,
                            state,
                            EXTRACT(EPOCH FROM (NOW() - write_lag)) AS write_lag_seconds,
                            EXTRACT(EPOCH FROM (NOW() - flush_lag)) AS flush_lag_seconds
                        FROM pg_stat_replication
                    """)
                )
                rows = result.fetchall()
                return {
                    "replicas": [dict(row._mapping) for row in rows],
                    "replica_count": len(rows),
                }
        except Exception as exc:
            return {"error": str(exc), "replicas": []}
