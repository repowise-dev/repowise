"""Tests for the cost-tracking opt-out and best-effort persistence (issue #326).

DB-backed cost tracking opens a *second* SQLAlchemy engine on ``wiki.db``. During
doc generation a writer is held on that file, so each per-call
``INSERT INTO llm_costs`` loses WAL's single-writer race and — with the default
30s ``busy_timeout`` — stalls generation for the full window. These tests cover
the two guards that fix that:

  1. ``--no-cost-tracking`` / ``REPOWISE_NO_COST_TRACKING`` skip the second
     engine entirely, returning an in-memory tracker.
  2. When tracking *is* enabled, the CLI tracker is *buffered*: per-call rows are
     held in memory and flushed in one transaction after generation, so cost
     writes never land inside the contended generation window. A short
     ``busy_timeout`` bounds the one flush as a last-resort defense.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from repowise.cli.providers.cost_tracking import (
    _COST_TRACKER_BUSY_TIMEOUT_MS,
    NO_COST_TRACKING_ENV,
    build_cost_tracker,
    cost_tracking_disabled,
)
from repowise.core.generation.cost_tracker import CostTracker


class TestCostTrackingDisabled:
    def test_flag_disables(self):
        assert cost_tracking_disabled(no_cost_tracking_flag=True) is True

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(NO_COST_TRACKING_ENV, raising=False)
        assert cost_tracking_disabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_env_truthy_disables(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv(NO_COST_TRACKING_ENV, value)
        assert cost_tracking_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
    def test_env_falsy_keeps_enabled(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv(NO_COST_TRACKING_ENV, value)
        assert cost_tracking_disabled() is False


class TestBuildCostTracker:
    def test_flag_returns_in_memory_tracker(self, tmp_path: Path):
        # Disabled → no second engine is opened, so the tracker has no
        # session_factory (in-memory only).
        tracker = build_cost_tracker(tmp_path, "repo", no_cost_tracking=True)
        assert isinstance(tracker, CostTracker)
        assert tracker._session_factory is None
        assert tracker._repo_id is None

    def test_env_returns_in_memory_tracker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(NO_COST_TRACKING_ENV, "1")
        tracker = build_cost_tracker(tmp_path, "repo")
        assert tracker._session_factory is None

    def test_disabled_build_opens_no_engine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # When disabled we must never touch open_repo_db / create_engine.
        import repowise.cli.providers.cost_tracking as ct

        def _boom(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("open_repo_db must not be called when disabled")

        monkeypatch.setattr(ct, "make_cost_tracker", _boom)
        tracker = build_cost_tracker(tmp_path, "repo", no_cost_tracking=True)
        assert tracker._session_factory is None


class TestEnabledTrackerUsesShortBusyTimeout:
    def test_short_busy_timeout_is_small(self):
        # Must be well under the 30s default so a contended cost insert can't
        # stall the generation writer for the full window.
        assert _COST_TRACKER_BUSY_TIMEOUT_MS < 30000
        assert _COST_TRACKER_BUSY_TIMEOUT_MS > 0

    @pytest.mark.asyncio
    async def test_make_cost_tracker_applies_short_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # make_cost_tracker must thread the short busy_timeout into open_repo_db.
        import repowise.cli.providers.cost_tracking as ct

        captured: dict = {}

        async def _fake_open_repo_db(repo_path, *, repo_name=None, busy_timeout_ms=None):
            captured["busy_timeout_ms"] = busy_timeout_ms
            return (object(), object(), "repo-id")

        import repowise.cli._repo_session as session_mod

        monkeypatch.setattr(session_mod, "open_repo_db", _fake_open_repo_db)
        tracker = await ct.make_cost_tracker(tmp_path, "repo")
        assert captured["busy_timeout_ms"] == _COST_TRACKER_BUSY_TIMEOUT_MS
        assert tracker._repo_id == "repo-id"


class TestContendedPersistIsBestEffort:
    @pytest.mark.asyncio
    async def test_record_drops_row_under_held_write_lock(self, tmp_path: Path) -> None:
        """With a competing writer holding the write lock, a DB-backed
        ``record()`` must still return without raising (the contended insert is
        dropped) and bound its wait to the short cost busy_timeout — never the
        30s default that wedged generation in issue #326."""
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )

        db_path = tmp_path / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

        # Short busy_timeout so the test stays fast while still proving the
        # fail-fast behavior.
        short_timeout_ms = 400
        engine = create_engine(db_url, busy_timeout_ms=short_timeout_ms)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            repo_id = repo.id

        tracker = CostTracker(session_factory=sf, repo_id=repo_id)

        # Hold the write lock on a separate raw connection.
        blocker = sqlite3.connect(db_path)
        blocker.execute("PRAGMA busy_timeout=0")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            # Must NOT raise even though the insert can't acquire the lock.
            cost = await tracker.record(
                model="deepseek-v4-flash",
                input_tokens=100,
                output_tokens=50,
                operation="doc_generation",
            )
            elapsed = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()

        # In-memory accounting still happened.
        assert cost > 0
        assert tracker.session_tokens == 150
        # Bounded by the short cost timeout, nowhere near the 30s default.
        assert elapsed < 5.0

        # The contended row was dropped, not persisted.
        async with get_session(sf) as session:
            import sqlalchemy as sa

            count = (await session.execute(sa.text("SELECT COUNT(*) FROM llm_costs"))).scalar()
        assert count == 0

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_record_persists_when_uncontended(self, tmp_path: Path) -> None:
        """Sanity check: without contention the row IS written, so the live
        ``repowise costs`` history keeps working when tracking is enabled."""
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )

        db_path = tmp_path / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        engine = create_engine(db_url, busy_timeout_ms=_COST_TRACKER_BUSY_TIMEOUT_MS)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            repo_id = repo.id

        tracker = CostTracker(session_factory=sf, repo_id=repo_id)
        await tracker.record(
            model="deepseek-v4-flash",
            input_tokens=100,
            output_tokens=50,
            operation="doc_generation",
        )

        async with get_session(sf) as session:
            import sqlalchemy as sa

            count = (await session.execute(sa.text("SELECT COUNT(*) FROM llm_costs"))).scalar()
        assert count == 1

        await engine.dispose()


async def _open_repo_engine(tmp_path: Path):
    """Open an initialised repo DB and return (engine, session_factory, repo_id, db_path)."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    db_path = tmp_path / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_engine(db_url, busy_timeout_ms=_COST_TRACKER_BUSY_TIMEOUT_MS)
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
        repo_id = repo.id
    return engine, sf, repo_id, db_path


async def _count_cost_rows(sf) -> int:
    import sqlalchemy as sa

    from repowise.core.persistence import get_session

    async with get_session(sf) as session:
        return (await session.execute(sa.text("SELECT COUNT(*) FROM llm_costs"))).scalar()


class TestBufferedTrackerDefersWrites:
    @pytest.mark.asyncio
    async def test_record_writes_nothing_until_flush(self, tmp_path: Path) -> None:
        """The whole point of the fix: a buffered ``record()`` performs NO DB
        write, so nothing can contend with the generation writer. Rows only
        land on ``flush()`` — in a single transaction."""
        engine, sf, repo_id, _ = await _open_repo_engine(tmp_path)
        tracker = CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)

        for _ in range(5):
            await tracker.record(
                model="deepseek-v4-flash",
                input_tokens=100,
                output_tokens=50,
                operation="doc_generation",
            )

        # Nothing written yet — all five rows are buffered in memory.
        assert await _count_cost_rows(sf) == 0
        assert tracker.session_tokens == 5 * 150  # live accounting still works

        written = await tracker.flush()
        assert written == 5
        assert await _count_cost_rows(sf) == 5

        # Flush is idempotent — buffer is drained.
        assert await tracker.flush() == 0
        assert await _count_cost_rows(sf) == 5

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_buffered_flush_succeeds_after_writer_releases(self, tmp_path: Path) -> None:
        """Even if a writer is held for the *entire* generation window, buffered
        records accumulate with zero contention and the post-generation flush
        (run after the writer releases) persists every row."""
        engine, sf, repo_id, db_path = await _open_repo_engine(tmp_path)
        tracker = CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)

        blocker = sqlite3.connect(db_path)
        blocker.execute("BEGIN IMMEDIATE")  # hold the write lock during "generation"
        try:
            for _ in range(3):
                # Must not block or raise — buffered records never touch the DB.
                await tracker.record(
                    model="deepseek-v4-flash",
                    input_tokens=10,
                    output_tokens=10,
                    operation="doc_generation",
                )
            assert await _count_cost_rows(sf) == 0
        finally:
            blocker.rollback()  # generation done → writer released
            blocker.close()

        assert await tracker.flush() == 3
        assert await _count_cost_rows(sf) == 3
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_flush_is_noop_for_in_memory_tracker(self) -> None:
        tracker = CostTracker()  # no session_factory
        await tracker.record(
            model="deepseek-v4-flash",
            input_tokens=10,
            output_tokens=10,
            operation="doc_generation",
        )
        assert await tracker.flush() == 0

    def test_flush_cost_tracker_helper_drives_flush(self, tmp_path: Path) -> None:
        """The sync ``flush_cost_tracker`` helper persists buffered rows from
        Click's synchronous context and never raises."""
        import asyncio

        from repowise.cli.providers import flush_cost_tracker

        engine, sf, repo_id, _ = asyncio.run(_open_repo_engine(tmp_path))
        tracker = CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)
        asyncio.run(
            tracker.record(
                model="deepseek-v4-flash",
                input_tokens=10,
                output_tokens=10,
                operation="doc_generation",
            )
        )
        assert flush_cost_tracker(tracker) == 1
        assert asyncio.run(_count_cost_rows(sf)) == 1
        asyncio.run(engine.dispose())
