"""PostgreSQL: plugin storage revisions under the advisory lock, and on a read-only connection.

Skipped unless a PostgreSQL URL is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_postgres_plugin_storage_revisions.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base
from repowise.core.registry import plugins
from repowise.core.registry import storage_migrations as sm
from repowise.core.registry.storage_migrations import (
    StorageMigration,
    StorageMigrationRegistry,
    ledger,
)

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set: PostgreSQL-only regression"
)


def _engine(**connect_args):
    return create_async_engine(PG_URL or "", poolclass=NullPool, connect_args=connect_args)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> StorageMigrationRegistry:
    # Everything is dropped, so refuse a database not named like a scratch one.
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    async def reset() -> None:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS acme_notes"))
            await conn.run_sync(ledger.metadata.drop_all)
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(reset())
    fresh = StorageMigrationRegistry()
    monkeypatch.setattr(sm, "storage_migrations", fresh)
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    plugins.reset()
    yield fresh
    plugins.reset()


_V1 = hashlib.sha256(b"v1").hexdigest()


def test_workers_starting_together_apply_a_revision_once(registry) -> None:
    calls: list[str] = []

    async def slow(conn) -> None:
        calls.append("v1")
        await asyncio.sleep(0.5)
        await conn.execute(text("CREATE TABLE acme_notes (id INTEGER PRIMARY KEY)"))

    registry.register(StorageMigration("acme.notes", "v1", _V1, slow))

    async def run() -> list[tuple[str, str]]:
        engines = [_engine() for _ in range(3)]
        try:
            await asyncio.gather(*(init_db(engine) for engine in engines))
            async with engines[0].connect() as conn:
                return [tuple(r) for r in await conn.execute(text(
                    f"SELECT namespace, revision FROM {ledger.name}"
                ))]
        finally:
            for engine in engines:
                await engine.dispose()

    assert asyncio.run(run()) == [("acme.notes", "v1")]
    assert calls == ["v1"]


def test_read_only_connection_leaves_the_revision_pending(registry) -> None:
    async def creates(conn) -> None:
        await conn.execute(text("CREATE TABLE acme_notes (id INTEGER PRIMARY KEY)"))

    registry.register(StorageMigration("acme.notes", "v1", _V1, creates))

    async def run() -> None:
        engine = _engine(server_settings={"default_transaction_read_only": "on"})
        try:
            await init_db(engine)  # does not raise
        finally:
            await engine.dispose()

    asyncio.run(run())
    [(store, namespace)] = registry._pending
    assert namespace == "acme.notes" and store.startswith("postgresql")
    assert "read-only" in registry._pending[(store, namespace)]
