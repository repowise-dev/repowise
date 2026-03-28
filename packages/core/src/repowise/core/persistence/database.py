"""Async database engine and session factory for repowise.

Supports two backends:
- SQLite (default): sqlite+aiosqlite:///path/to/file.db
- PostgreSQL:       postgresql+asyncpg://user:pass@host/dbname

Call get_db_url() to normalise raw URLs (adds the async driver prefix).
Call create_engine() to create an AsyncEngine.
Call init_db() once at startup to create all tables and the FTS index.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.sql import text

from .models import Base

__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "async_sessionmaker",
    "create_engine",
    "create_session_factory",
    "get_db_url",
    "get_session",
    "init_db",
]

_DEFAULT_URL = "sqlite+aiosqlite:///.repowise/wiki.db"


def get_db_url(raw_url: str | None = None) -> str:
    """Normalise a database URL to include the async driver prefix.

    - ``sqlite:///...``      → ``sqlite+aiosqlite:///...``
    - ``postgresql://...``   → ``postgresql+asyncpg://...``
    - ``postgresql+psycopg://...`` → unchanged (explicit driver wins)
    - Already async-prefixed URLs are returned as-is.
    - ``None`` → default SQLite path: ``sqlite+aiosqlite:///.repowise/wiki.db``
    """
    if raw_url is None:
        return _DEFAULT_URL

    url = raw_url.strip()

    if url.startswith("sqlite://") and "aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url.replace("://", "+asyncpg://", 1)

    return url


def create_engine(
    url: str | None = None,
    *,
    echo: bool = False,
    # StaticPool is required for :memory: SQLite so all connections share the same DB.
    # Pass use_static_pool=True explicitly when creating in-memory test engines.
    use_static_pool: bool = False,
) -> AsyncEngine:
    """Create an AsyncEngine for the given database URL.

    Args:
        url:             Raw or async-prefixed database URL.  Defaults to SQLite.
        echo:            Log all SQL statements (useful for debugging).
        use_static_pool: Force StaticPool (required for in-memory SQLite tests).
    """
    db_url = get_db_url(url)
    is_sqlite = db_url.startswith("sqlite")

    kwargs: dict = {"echo": echo}

    if is_sqlite:
        # SQLite requires check_same_thread=False for multi-threaded async use
        kwargs["connect_args"] = {"check_same_thread": False}
        if use_static_pool or ":memory:" in db_url:
            # StaticPool: all connect() calls return the same connection.
            # Mandatory for in-memory SQLite — without it each call gets a fresh DB.
            kwargs["poolclass"] = StaticPool
        else:
            kwargs["poolclass"] = NullPool
    else:
        # PostgreSQL — asyncpg handles its own connection pool
        kwargs["pool_pre_ping"] = True

    return create_async_engine(db_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async_sessionmaker bound to *engine*.

    expire_on_commit=False: prevents SQLAlchemy from expiring attributes after
    commit, which would require a sync lazy-load (impossible in async context).
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a session and handles commit/rollback."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(engine: AsyncEngine) -> None:
    """Create all SQLAlchemy tables and the FTS index for the given engine.

    Safe to call on an already-initialised database (idempotent).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # SQLite-only: create FTS5 virtual table for full-text search.
        # PostgreSQL uses a GIN index added by the Alembic migration.
        if engine.dialect.name == "sqlite":
            await conn.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS page_fts "
                    "USING fts5(page_id UNINDEXED, title, content)"
                )
            )
