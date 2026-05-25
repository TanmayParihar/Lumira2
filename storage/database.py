"""
Async SQLAlchemy engine + session factory.
Provides get_session() dependency for FastAPI and direct use in workers.

NOTE ON CELERY WORKERS
----------------------
Celery tasks call asyncio.run() which creates a new event loop per invocation.
A pooled engine is bound to the event loop it was created in; reusing it across
loops causes "Future attached to a different loop" errors.

Solution: workers import get_worker_session() which uses NullPool — no pooling,
each asyncio.run() gets a fresh TCP connection.  FastAPI uses the normal pooled
engine (long-lived single event loop).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config.settings import settings
from storage.models import Base

logger = structlog.get_logger(__name__)

# ── Pooled engine — used by FastAPI (single long-lived event loop) ─────────
engine = create_async_engine(
    settings.postgres_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# _worker_engine is intentionally NOT defined at module level.
# See get_worker_session() below.


async def create_all_tables() -> None:
    """Create all tables (run once at startup)."""
    async with engine.begin() as conn:
        # Ensure PostGIS extension exists
        await conn.execute(
            __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS postgis")
        )
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database.tables_created")


async def drop_all_tables() -> None:
    """Drop all tables — DESTRUCTIVE, only for dev/test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.warning("database.tables_dropped")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager that yields an AsyncSession, safe for any event loop.

    Creates a fresh NullPool engine *inside* the running coroutine so that
    every asyncpg connection is bound to the current event loop.  This is the
    correct pattern for Celery tasks (each asyncio.run() = new loop) AND for
    one-off scripts.

    FastAPI HTTP handlers use get_db() below, which re-uses the long-lived
    pooled engine for efficiency.
    """
    _eng = create_async_engine(settings.postgres_url, echo=False, poolclass=NullPool)
    _fac = async_sessionmaker(_eng, class_=AsyncSession, expire_on_commit=False)
    try:
        async with _fac() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await _eng.dispose()


# Alias — workers import get_worker_session; internally it's the same thing.
get_worker_session = get_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a session per request."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
