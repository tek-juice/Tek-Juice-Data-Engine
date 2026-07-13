"""
DATA ENGINE — Database Configuration
SQLAlchemy async engine, session factory, and base model setup.
Includes PGVector extension registration.
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from configs.settings import get_settings

settings = get_settings()


# ── Engine Factory

def _build_engine_kwargs() -> dict[str, Any]:
    """Build engine kwargs based on environment."""
    base: dict[str, Any] = {
        "echo": settings.postgres_echo,
        "future": True,
    }
    if settings.is_production:
        base.update(
            {
                "poolclass": AsyncAdaptedQueuePool,
                "pool_size": settings.postgres_pool_size,
                "max_overflow": settings.postgres_max_overflow,
                "pool_timeout": settings.postgres_pool_timeout,
                "pool_pre_ping": True,
                "pool_recycle": 3600,
            }
        )
    else:
        # Use NullPool in tests / development to avoid connection leaks
        base["poolclass"] = NullPool

    return base


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **_build_engine_kwargs(),
)

# Session factory — expire_on_commit=False keeps objects usable after commit
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Base Model 

class Base(DeclarativeBase):
    """Base class for all ORM models."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise model to dictionary."""
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }


# ── PGVector Extension 

async def enable_pgvector(conn: Any) -> None:
    """Enable the pgvector extension if not already present."""
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


# ── Session Dependency

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Database Lifecycle 

async def init_db() -> None:
    """
    Initialise the database — enables extensions and creates all tables.
    Called once at application startup.
    """
    async with engine.begin() as conn:
        await enable_pgvector(conn)
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """
    Dispose the engine connection pool.
    Called at application shutdown.
    """
    await engine.dispose()


# ── Health Check 

async def check_db_health() -> bool:
    """Return True if database is reachable and responding."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
