"""PostgreSQL: processes opening one empty database together all start.

A server and a worker started together each run ``init_db`` and
``FullTextSearch.ensure_index`` against the same fresh database. Without a
lock both see ``repositories`` missing, both issue ``CREATE TABLE``, and the
second fails on ``pg_type_typname_nsp_index``.

Skipped unless a PostgreSQL URL is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_postgres_concurrent_first_start.py
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base
from repowise.core.persistence.search import FullTextSearch

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set: PostgreSQL-only regression"
)

STARTERS = 6


def _engine():
    return create_async_engine(PG_URL or "", poolclass=NullPool)


@pytest.fixture
def empty_pg() -> None:
    # Everything is dropped, so refuse a database not named like a scratch one.
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    async def drop() -> None:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(drop())


async def _start(engine) -> None:
    await init_db(engine)
    await FullTextSearch(engine).ensure_index()


def _schema() -> tuple[set[str], dict[str, set[str]]]:
    async def read() -> tuple[set[str], dict[str, set[str]]]:
        engine = _engine()
        async with engine.connect() as conn:

            def collect(sync_conn) -> tuple[set[str], dict[str, set[str]]]:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                indexes = {
                    name: {i["name"] for i in inspector.get_indexes(name)}
                    for name in tables
                    if name in Base.metadata.tables
                }
                return tables, indexes

            result = await conn.run_sync(collect)
        await engine.dispose()
        return result

    return asyncio.run(read())


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_first_starts_all_succeed(empty_pg: None, attempt: int) -> None:
    async def run() -> list[BaseException | None]:
        engines = [_engine() for _ in range(STARTERS)]
        try:
            return await asyncio.gather(
                *(_start(engine) for engine in engines), return_exceptions=True
            )
        finally:
            for engine in engines:
                await engine.dispose()

    errors = [r for r in asyncio.run(run()) if isinstance(r, BaseException)]
    assert errors == []

    tables, indexes = _schema()
    assert set(Base.metadata.tables) <= tables
    for name, table in Base.metadata.tables.items():
        assert {i.name for i in table.indexes} <= indexes[name], name
    assert "idx_wiki_pages_fts" in indexes["wiki_pages"]
