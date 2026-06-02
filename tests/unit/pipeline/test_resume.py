"""Unit tests for the pipeline resume building blocks (issue #341).

Covers the durable phase ledger, git-metadata rehydration, and the
controller's skip-decision logic against an in-memory SQLite database. The
full checkpoint→rehydrate-graph round-trip is exercised by the resume
integration test once wiring lands.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence import get_session
from repowise.core.persistence.crud import upsert_git_metadata_bulk, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.pipeline.resume import (
    ResumeController,
    ResumeLedger,
    ResumePhase,
    rehydrate_git_meta_map,
)


@pytest.fixture
async def sf():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


async def _make_repo(sf) -> str:
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name="r", local_path="/tmp/r")
        return repo.id


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


async def test_ledger_marks_and_reads_completed(sf):
    repo_id = await _make_repo(sf)
    ledger = ResumeLedger(sf, repo_id)

    assert await ledger.completed_phases() == set()

    await ledger.mark_started(ResumePhase.INDEX)
    # RUNNING is not COMPLETED.
    assert await ledger.completed_phases() == set()

    await ledger.mark_completed(ResumePhase.INDEX)
    assert ResumePhase.INDEX in await ledger.completed_phases()


async def test_ledger_completion_survives_a_fresh_ledger(sf):
    """A new ledger (simulating a re-run process) sees prior completions."""
    repo_id = await _make_repo(sf)
    await ResumeLedger(sf, repo_id).mark_completed(ResumePhase.INDEX)

    fresh = ResumeLedger(sf, repo_id)
    assert ResumePhase.INDEX in await fresh.completed_phases()


# ---------------------------------------------------------------------------
# git_meta_map rehydration
# ---------------------------------------------------------------------------


async def test_rehydrate_git_meta_map_round_trips(sf):
    repo_id = await _make_repo(sf)
    rows = [
        {
            "file_path": "src/a.py",
            "commit_count_total": 42,
            "is_hotspot": True,
            "co_change_partners_json": '[{"file_path": "src/b.py", "co_change_count": 3}]',
        },
        {"file_path": "src/b.py", "commit_count_total": 7, "is_hotspot": False},
    ]
    async with get_session(sf) as session:
        await upsert_git_metadata_bulk(session, repo_id, rows)

    async with get_session(sf) as session:
        meta = await rehydrate_git_meta_map(session, repo_id)

    assert set(meta) == {"src/a.py", "src/b.py"}
    assert meta["src/a.py"]["commit_count_total"] == 42
    assert meta["src/a.py"]["is_hotspot"] is True
    # JSON columns come back as their stored strings (consumers json.loads them).
    assert "src/b.py" in meta["src/a.py"]["co_change_partners_json"]
    # No blame_index is ever persisted.
    assert "blame_index" not in meta["src/a.py"]


async def test_rehydrate_git_meta_map_empty_when_unindexed(sf):
    repo_id = await _make_repo(sf)
    async with get_session(sf) as session:
        assert await rehydrate_git_meta_map(session, repo_id) == {}


# ---------------------------------------------------------------------------
# Controller skip decisions
# ---------------------------------------------------------------------------


async def test_controller_no_skip_without_resume(sf):
    repo_id = await _make_repo(sf)
    await ResumeLedger(sf, repo_id).mark_completed(ResumePhase.INDEX)

    ctrl = ResumeController(sf, repo_id, resume=False)
    assert await ctrl.can_skip(ResumePhase.INDEX) is False


async def test_controller_skips_completed_index_on_resume(sf):
    repo_id = await _make_repo(sf)
    await ResumeLedger(sf, repo_id).mark_completed(ResumePhase.INDEX)

    ctrl = ResumeController(sf, repo_id, resume=True)
    assert await ctrl.can_skip(ResumePhase.INDEX) is True
    # ANALYSIS not completed → cannot skip it (prefix rule).
    assert await ctrl.can_skip(ResumePhase.ANALYSIS) is False


async def test_controller_prefix_rule(sf):
    """A later phase is only skippable when every earlier phase also completed."""
    repo_id = await _make_repo(sf)
    ledger = ResumeLedger(sf, repo_id)
    # ANALYSIS completed but INDEX not → analysis must NOT be skipped.
    await ledger.mark_completed(ResumePhase.ANALYSIS)

    ctrl = ResumeController(sf, repo_id, resume=True)
    assert await ctrl.can_skip(ResumePhase.ANALYSIS) is False
