"""
DATA ENGINE — Database Configuration
SQLAlchemy async engine, session factory, and base model setup.
Includes PGVector extension registration.

IMPORTANT — lazy initialisation
---------------------------------
The engine and session factory are created on first use (not at import time).
This ensures Docker environment variable overrides (PGBOUNCER_HOST etc.) are
fully applied before the database URL is read from settings.

All public names (engine, AsyncSessionLocal) are replaced by module-level
properties via a thin accessor pattern so existing import sites keep working:

    from configs.database import get_db_session, init_db, check_db_health
    from configs.database import engine          # works, returns lazy engine
    from configs.database import AsyncSessionLocal  # works
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool


# ── Lazy engine & session factory ─────────────────────────────────────────────
# Stored as module-level variables but only populated on first use.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def _build_engine_kwargs(settings: Any) -> dict[str, Any]:
    """Build SQLAlchemy engine kwargs from settings."""
    base: dict[str, Any] = {
        "echo":   settings.postgres_echo,
        "future": True,
    }
    if settings.pgbouncer_enabled:
        # PgBouncer transaction mode — SQLAlchemy must NOT pool connections.
        base["poolclass"] = NullPool
    elif settings.is_production:
        base.update({
            "poolclass":    AsyncAdaptedQueuePool,
            "pool_size":    settings.postgres_pool_size,
            "max_overflow": settings.postgres_max_overflow,
            "pool_timeout": settings.postgres_pool_timeout,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        })
    else:
        base["poolclass"] = NullPool
    return base


def _get_engine() -> AsyncEngine:
    """Return the shared engine, creating it on first call."""
    global _engine
    if _engine is None:
        import logging
        from configs.settings import get_settings
        s = get_settings()
        url = s.database_url
        # Log the URL (password masked) so startup issues are immediately visible
        masked = url.replace(s.postgres_password, "***")
        logging.getLogger("database").info("Building database engine → %s", masked)
        _engine = create_async_engine(url, **_build_engine_kwargs(s))
    return _engine


def _get_session_factory() -> async_sessionmaker:
    """Return the shared session factory, creating it on first call."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


# ── Backward-compatible module-level accessors ────────────────────────────────
# Code that does `from configs.database import engine` or
# `from configs.database import AsyncSessionLocal` still works.

class _LazyEngine:
    """Proxy that forwards attribute access to the real engine."""
    def __getattr__(self, name: str):
        return getattr(_get_engine(), name)

    # Forward the two most common call patterns
    def begin(self):
        return _get_engine().begin()

    def connect(self):
        return _get_engine().connect()

    async def dispose(self):
        return await _get_engine().dispose()


class _LazySessionFactory:
    """Proxy that forwards calls to the real session factory."""
    def __call__(self, *args, **kwargs):
        return _get_session_factory()(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(_get_session_factory(), name)


engine: AsyncEngine = _LazyEngine()           # type: ignore[assignment]
AsyncSessionLocal = _LazySessionFactory()


# ── Base Model ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Base class for all ORM models."""

    def to_dict(self) -> dict[str, Any]:
        return {
            col.name: getattr(self, col.name)
            for col in self.__table__.columns
        }


# ── PGVector Extension ────────────────────────────────────────────────────────

async def enable_pgvector(conn: Any) -> None:
    """Enable vector, pg_trgm, and pgcrypto extensions."""
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


# ── Session Dependency ────────────────────────────────────────────────────────

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a managed async database session."""
    async with _get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Database Lifecycle ────────────────────────────────────────────────────────

async def init_db() -> None:
    """Enable extensions and create all tables. Called at app startup."""
    async with _get_engine().begin() as conn:
        await enable_pgvector(conn)
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    """Dispose the engine connection pool. Called at app shutdown."""
    if _engine is not None:
        await _engine.dispose()


# ── Health Check ──────────────────────────────────────────────────────────────

async def check_db_health() -> bool:
    """Return True if the database is reachable."""
    try:
        async with _get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
