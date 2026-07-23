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

import asyncio
import logging
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

_log = logging.getLogger(__name__)


#  Lazy engine & session factory 
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
    """Return the shared engine, building it fresh on every call."""
    global _engine
    from configs.settings import get_settings
    from sqlalchemy.pool import NullPool
    s = get_settings()
    _engine = create_async_engine(
        s.database_url,
        echo=False,
        future=True,
        poolclass=NullPool,
    )
    return _engine


def _get_session_factory() -> async_sessionmaker:
    """Return the shared session factory, always rebuilt from current engine."""
    global _session_factory
    _session_factory = async_sessionmaker(
        bind=_get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    return _session_factory


#  Backward-compatible module-level accessors
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


#  Base Model 

class Base(DeclarativeBase):
    """Base class for all ORM models."""

    def to_dict(self) -> dict[str, Any]:
        return {
            col.name: getattr(self, col.name)
            for col in self.__table__.columns
        }


#  PGVector Extension
async def enable_pgvector(conn: Any) -> None:
    """Enable vector, pg_trgm, and pgcrypto extensions."""
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


#  Session Dependency

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a managed async database session.

    Converts low-level connection errors (DB unreachable) into
    ServiceUnavailableError so the exception handlers return a clean
    503 JSON response rather than propagating a raw OSError through
    the middleware stack.
    """
    from shared.exceptions.base import ServiceUnavailableError
    try:
        async with _get_session_factory()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    except ServiceUnavailableError:
        raise
    except OSError as exc:
        _log.warning("db_connection_refused: %s", exc)
        raise ServiceUnavailableError(
            "Database is unreachable. Start Docker services: "
            "docker compose up -d postgres pgbouncer"
        ) from exc


#  Database Lifecycle

async def _init_db_core(retries: int, delay: float) -> None:
    """
    Internal: attempt DB init with retries, log CRITICAL on final failure.
    Never raises — callers (init_db background task) log and continue.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with _get_engine().begin() as conn:
                await enable_pgvector(conn)
                await conn.run_sync(Base.metadata.create_all)
            _log.info("Database initialised successfully.")
            return
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                _log.warning(
                    "Database not ready (attempt %d/%d): %s — retrying in %.0fs…",
                    attempt, retries, exc, delay,
                )
                await asyncio.sleep(delay)

    _log.critical(
        "Database unreachable after %d attempts. Last error: %s\n"
        "  → Make sure PostgreSQL (or PgBouncer) is running and "
        "PGBOUNCER_HOST / POSTGRES_HOST point to the correct host.\n"
        "  → Local dev:  docker compose up -d postgres pgbouncer redis",
        retries, last_exc,
    )


async def init_db(retries: int = 5, delay: float = 2.0) -> None:
    """
    Schedule DB initialisation as a background task and return immediately.

    Every service calls ``await init_db()`` in its lifespan.  By scheduling
    the work as a background asyncio task the service process binds its port
    and starts accepting connections right away — even when the database
    container is still coming up.  DB-dependent endpoints return 503 via
    ``get_db_session`` until the connection succeeds.
    """
    asyncio.create_task(_init_db_core(retries, delay))


async def dispose_db() -> None:
    """Dispose the engine connection pool. Called at app shutdown."""
    if _engine is not None:
        await _engine.dispose()


#  Health Check 

async def check_db_health() -> bool:
    """Return True if the database is reachable."""
    try:
        async with _get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
