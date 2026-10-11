"""PostgreSQL regression for issue #2062: the cost tracker's engine does not
survive the ``asyncio.run()`` boundary it is built in, but ``run_repo_generation``
opens it, generates, and flushes across three separate ``asyncio.run()`` calls.

SQLite's ``NullPool`` never holds a live connection between checkouts, so this
only shows up against a real Postgres pool.

Skipped unless a PostgreSQL URL is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_cost_tracker_cross_loop_engine.py
"""

from __future__ import annotations

import asyncio
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.core.persistence.models import Base

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set: PostgreSQL-only regression"
)


@pytest.fixture
def pg_repo(tmp_path, monkeypatch):
    # Same refusal as test_postgres_symbol_lengths.py's pg_session: this
    # fixture drops every table, so it insists on a scratch-named database.
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    async def _reset() -> None:
        engine = create_async_engine(PG_URL or "", poolclass=None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_reset())
    monkeypatch.setenv("REPOWISE_DB_URL", PG_URL or "")
    return tmp_path


def test_flush_persists_rows_recorded_after_a_separate_build_loop(pg_repo) -> None:
    """Exercises the real sequence ``run_repo_generation`` uses: ``build_cost_tracker``
    (asyncio.run #1, opens the engine), a buffered ``record()`` call standing in for
    generation (asyncio.run #2), then ``flush_cost_tracker`` (asyncio.run #3). Fails on
    main: the pooled asyncpg connection ``build_cost_tracker`` opened dies with its
    event loop, and the flush silently drops the row instead of raising."""
    from repowise.cli.providers.cost_tracking import build_cost_tracker, flush_cost_tracker

    tracker = build_cost_tracker(pg_repo, "repo")
    assert tracker._session_factory is not None  # sanity: a real Postgres engine was built

    asyncio.run(
        tracker.record(
            model="claude-sonnet-5",
            input_tokens=100,
            output_tokens=50,
            operation="doc_generation",
        )
    )

    written = flush_cost_tracker(tracker)
    assert written == 1

    # A fresh engine/connection, deliberately not `tracker._session_factory`:
    # reusing the tracker's engine here would be a fourth asyncio.run() reusing
    # a connection flush's third one pooled, which is the same cross-loop
    # pattern the fix addresses, not a check on it.
    async def _count() -> int:
        engine = create_async_engine(PG_URL or "", poolclass=None)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(sa.text("SELECT COUNT(*) FROM llm_costs"))).scalar()
        finally:
            await engine.dispose()

    assert asyncio.run(_count()) == 1
