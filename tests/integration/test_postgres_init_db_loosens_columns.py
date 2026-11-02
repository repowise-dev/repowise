"""PostgreSQL regression for issue #3063: ``init_db`` loosens columns the model
loosened, so a database that never runs Alembic still accepts what the code
writes.

The Docker image starts the API with ``init_db`` and no ``alembic upgrade``.
Migration 0094 made ``health_file_metrics.score``, ``max_ccn`` and
``max_nesting`` nullable on PostgreSQL, so a database left at 0093 rejected
the NULL score v0.55 stores for a file health cannot analyse, and the sync
failed. SQLite never hit this: ``init_db`` rebuilds that table there.

Skipped unless a PostgreSQL URL with pgvector is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_postgres_init_db_loosens_columns.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set: PostgreSQL-only regression"
)

CORE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "core"


def _columns(table: str) -> dict[str, dict]:
    async def read() -> dict[str, dict]:
        engine = create_async_engine(PG_URL or "")
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: inspect(c).get_columns(table))
        await engine.dispose()
        return {c["name"]: c for c in cols}

    return asyncio.run(read())


def _execute(*statements: str) -> None:
    async def run() -> None:
        engine = create_async_engine(PG_URL or "")
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
        await engine.dispose()

    asyncio.run(run())


def _init_db() -> None:
    async def run() -> None:
        engine = create_async_engine(PG_URL or "")
        await init_db(engine)
        await engine.dispose()

    asyncio.run(run())


@pytest.fixture
def empty_pg() -> None:
    # Everything is dropped, so refuse a database not named like a scratch one.
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    async def drop() -> None:
        engine = create_async_engine(PG_URL or "")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()

    asyncio.run(drop())


def test_database_at_0093_accepts_unscored_health_rows(
    empty_pg: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database migrated to 0093 is NOT NULL on the score columns until
    init_db runs, and nullable after it, with no Alembic step."""
    monkeypatch.setenv("DATABASE_URL", PG_URL or "")
    monkeypatch.chdir(CORE_ROOT)
    # env.py's fileConfig() resets stdlib logging, which breaks caplog later.
    with patch("logging.config.fileConfig"):
        command.upgrade(Config("alembic.ini"), "0093")
    before = _columns("health_file_metrics")
    assert not any(before[name]["nullable"] for name in ("score", "max_ccn", "max_nesting"))

    _init_db()

    after = _columns("health_file_metrics")
    assert all(after[name]["nullable"] for name in ("score", "max_ccn", "max_nesting"))
    _init_db()  # idempotent once loosened


def test_init_db_loosens_without_alembic(empty_pg: None) -> None:
    """The stores Docker creates never see Alembic: a NOT NULL the model dropped
    and a VARCHAR the model widened are both brought up to the model."""
    _init_db()
    _execute(
        "UPDATE dead_code_findings SET lines = 0 WHERE lines IS NULL",
        "ALTER TABLE dead_code_findings ALTER COLUMN lines SET NOT NULL",
        "ALTER TABLE git_function_blame ALTER COLUMN symbol_id TYPE VARCHAR(512)",
    )

    _init_db()

    assert _columns("dead_code_findings")["lines"]["nullable"]
    assert _columns("git_function_blame")["symbol_id"]["type"].length is None
