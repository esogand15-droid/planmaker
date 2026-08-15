"""Async engine / session factory with production pooling and startup retry."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import settings
from .models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str | None = None, echo: bool | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    dsn = url or settings.database_url
    kwargs: dict = {
        "echo": settings.sql_echo if echo is None else echo,
        "pool_pre_ping": True,  # survives Railway/Postgres idle disconnects
    }
    if not dsn.startswith("sqlite"):
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle,
            pool_timeout=30,
        )
    _engine = create_async_engine(dsn, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_engine() -> AsyncEngine:
    return _engine or init_engine()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def wait_for_database(
    retries: int | None = None, base_delay: float = 1.0, max_delay: float = 15.0
) -> None:
    """Databases boot slower than apps — retry with exponential backoff."""
    engine = get_engine()
    attempts = retries if retries is not None else settings.db_connect_retries
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            log.info("database connection established (attempt %s)", attempt)
            return
        except Exception as exc:
            if attempt >= attempts:
                log.error("database unreachable after %s attempts: %s", attempts, exc)
                raise
            log.warning(
                "database not ready (attempt %s/%s): %s — retrying in %.1fs",
                attempt, attempts, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def create_all(engine: AsyncEngine | None = None) -> None:
    """Dev/test bootstrap only — production schema is managed by Alembic."""
    eng = engine or get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Close every pooled connection on shutdown (no half-open sockets)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        log.info("database pool disposed")
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
