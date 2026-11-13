"""PostgreSQL regression: ``performance_opportunities.cost_proof`` stores every
proof the queue writes.

The column was ``VARCHAR(16)`` and ``background_unproven`` is 19 characters,
so PostgreSQL rejected the insert at the end of an index run. SQLite ignores
``VARCHAR`` length, so it never showed there.

Skipped unless a PostgreSQL URL with pgvector is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \
        uv run pytest tests/integration/test_postgres_performance_cost_proof.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.analysis.health.queue.eligibility import QUEUE_PROOFS
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base, PerformanceOpportunity, Repository, _new_uuid

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set: PostgreSQL-only regression"
)

CORE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "core"


def _cost_proof_length() -> int | None:
    async def read() -> int | None:
        engine = create_async_engine(PG_URL or "")
        async with engine.connect() as conn:
            cols = await conn.run_sync(
                lambda c: inspect(c).get_columns("performance_opportunities")
            )
        await engine.dispose()
        return next(c["type"].length for c in cols if c["name"] == "cost_proof")

    return asyncio.run(read())


def _alembic_upgrade(revision: str) -> None:
    # env.py's fileConfig() resets stdlib logging, which breaks caplog later.
    with patch("logging.config.fileConfig"):
        command.upgrade(Config("alembic.ini"), revision)


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


def test_every_queue_proof_persists(empty_pg: None) -> None:
    """Each value ``queue_proof`` can return round-trips through the column,
    including on a database left at the old width that never runs Alembic:
    ``init_db`` widens it in place."""
    assert "background_unproven" in QUEUE_PROOFS

    async def run() -> list[str]:
        engine = create_async_engine(PG_URL or "")
        await init_db(engine)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE performance_opportunities ALTER COLUMN cost_proof TYPE VARCHAR(16)"
                )
            )
        await init_db(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            repo = Repository(id=_new_uuid(), name="repro", local_path="/tmp/repro", url="")
            session.add(repo)
            await session.flush()
            for proof in QUEUE_PROOFS:
                session.add(
                    PerformanceOpportunity(
                        repository_id=repo.id, opportunity_id=f"perf_{proof}", cost_proof=proof
                    )
                )
            await session.commit()
            stored = await session.scalars(
                select(PerformanceOpportunity.cost_proof).where(
                    PerformanceOpportunity.repository_id == repo.id
                )
            )
            result = sorted(stored)
        await engine.dispose()
        return result

    assert asyncio.run(run()) == sorted(QUEUE_PROOFS)


def test_migration_widens_existing_column(empty_pg: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A database migrated past 0099 holds VARCHAR(16) until 0123 widens it."""
    monkeypatch.setenv("DATABASE_URL", PG_URL or "")
    monkeypatch.chdir(CORE_ROOT)
    _alembic_upgrade("0122")
    assert _cost_proof_length() == 16

    _alembic_upgrade("0123")

    assert _cost_proof_length() == 32
