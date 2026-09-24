"""
DATA ENGINE - Trend Embedding Backfill
========================================
Finds scraped_trends rows with no embedding yet, sends their text to the
embedding service, and writes the resulting vector back to the row.

Run this INSIDE a container that already has configs.database and the
rest of the app's dependencies available (e.g. data_engine_gap) - it will
not run correctly on the bare EC2 host.

Usage (from inside the container):
    python /app/tools/backfill_trend_embeddings.py --limit 1000 --batch-size 50
"""

import argparse
import asyncio

import httpx
import structlog
from sqlalchemy import text

from configs.database import AsyncSessionLocal
from configs.settings import get_settings

logger = structlog.get_logger(__name__)

EMBEDDING_SERVICE_URL = "http://embedding_service:8003/api/v1/embed"
DEFAULT_BATCH_SIZE = 50


async def backfill(limit: int, batch_size: int) -> None:
    settings = get_settings()
    dims = settings.embedding_dimension
    col = f"embedding_{dims}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(f"""
                SELECT id, title, snippet
                FROM scraped_trends
                WHERE {col} IS NULL
                ORDER BY scraped_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        rows = result.fetchall()

    if not rows:
        print(f"No rows need embeddings (column: {col}).")
        return

    print(f"Found {len(rows)} rows needing embeddings (column: {col})")

    total_embedded = 0
    total_failed = 0

    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            texts = [
                (f"{r.title or ''} {r.snippet or ''}".strip() or "untitled")
                for r in batch
            ]

            try:
                resp = await client.post(EMBEDDING_SERVICE_URL, json={"texts": texts})
                resp.raise_for_status()
                embeddings = resp.json()["embeddings"]
            except Exception as exc:
                logger.warning(
                    "embedding_batch_failed",
                    error=str(exc),
                    batch_start=i,
                    batch_size=len(batch),
                )
                total_failed += len(batch)
                continue

            async with AsyncSessionLocal() as session:
                for row, vec in zip(batch, embeddings):
                    await session.execute(
                        text(f"UPDATE scraped_trends SET {col} = CAST(:vec AS vector) WHERE id = :id"),
                        {"vec": str(vec), "id": row.id},
                    )
                await session.commit()

            total_embedded += len(batch)
            print(f"  embedded {total_embedded}/{len(rows)} (failed so far: {total_failed})")

    print(f"Done. Embedded {total_embedded} rows, {total_failed} failed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill embeddings for scraped_trends rows.")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    asyncio.run(backfill(limit=args.limit, batch_size=args.batch_size))
