"""Integration test for `repowise init --resume` (issue #341).

Proves the resume contract end-to-end against the real pipeline + sample
repo: a first run persists the INDEX phase, and a resumed run reuses it —
rehydrating the graph + git metadata and only re-parsing source, *without*
re-running git indexing or the full ingestion/graph-build. That is the
minutes-long work the "safe to Ctrl-C, then --resume" promise exists to skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence import get_session
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.pipeline import run_pipeline
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.pipeline.resume import ResumeLedger, ResumePhase
from repowise.core.pipeline.resume.controller import ResumeController


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


async def _make_repo(sf, path: Path) -> str:
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name="sample", local_path=str(path))
        return repo.id


async def test_first_run_checkpoints_index(sf, sample_repo_path: Path) -> None:
    repo_id = await _make_repo(sf, sample_repo_path)
    ctrl = ResumeController(sf, repo_id, resume=False)

    result = await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ctrl,
    )
    assert result.parsed_files, "expected a parsed sample repo"

    # INDEX was persisted + recorded so a later run can resume.
    completed = await ResumeLedger(sf, repo_id).completed_phases()
    assert ResumePhase.INDEX in completed


async def test_resume_skips_index_compute(sf, sample_repo_path: Path, monkeypatch) -> None:
    repo_id = await _make_repo(sf, sample_repo_path)

    # First run persists the INDEX phase.
    await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ResumeController(sf, repo_id, resume=False),
    )

    # On the resumed run, the expensive ingestion + git indexing must NOT run.
    # Patch both to blow up so the test fails loudly if the skip path regresses.
    import repowise.core.pipeline.orchestrator as orch

    async def _boom_ingestion(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("_run_ingestion ran on resume — index was not skipped")

    async def _boom_git(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("_run_git_indexing ran on resume — index was not skipped")

    monkeypatch.setattr(orch, "_run_ingestion", _boom_ingestion)
    monkeypatch.setattr(orch, "_run_git_indexing", _boom_git)

    result = await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ResumeController(sf, repo_id, resume=True),
    )

    # The resumed run still produced a usable index: graph rehydrated from the
    # DB and source re-parsed.
    assert result.parsed_files, "resume should re-parse source files"
    assert result.graph_builder.graph().number_of_nodes() > 0, "graph should be rehydrated"


async def test_first_run_checkpoints_analysis(sf, sample_repo_path: Path) -> None:
    repo_id = await _make_repo(sf, sample_repo_path)
    await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ResumeController(sf, repo_id, resume=False),
    )

    # ANALYSIS is checkpointed mid-run (before generation), so a later
    # generation-interrupted resume can skip recomputing it.
    completed = await ResumeLedger(sf, repo_id).completed_phases()
    assert ResumePhase.ANALYSIS in completed


async def test_resume_skips_analysis_recompute(sf, sample_repo_path: Path, monkeypatch) -> None:
    repo_id = await _make_repo(sf, sample_repo_path)

    # First run persists INDEX + ANALYSIS.
    await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ResumeController(sf, repo_id, resume=False),
    )

    # On resume, none of the analysis recompute paths may run — patch all three
    # to fail loudly if the skip regresses.
    import repowise.core.pipeline.orchestrator as orch

    async def _boom_dead_code(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("dead-code analysis ran on resume — analysis not skipped")

    async def _boom_health(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("health analysis ran on resume — analysis not skipped")

    async def _boom_decisions(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("decision extraction ran on resume — analysis not skipped")

    monkeypatch.setattr(orch, "_run_dead_code_analysis", _boom_dead_code)
    monkeypatch.setattr(orch, "_run_health_analysis", _boom_health)
    monkeypatch.setattr(orch, "_run_decision_extraction", _boom_decisions)

    result = await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        resume_controller=ResumeController(sf, repo_id, resume=True),
    )
    assert result.parsed_files, "resume should still produce a usable index"
