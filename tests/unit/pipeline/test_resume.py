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
from repowise.core.persistence._interfaces.job_store import ABANDONED_JOB_ERROR, JobState
from repowise.core.persistence.crud import upsert_git_metadata_bulk, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.stores.sql_job_store import SqlJobStore
from repowise.core.pipeline.resume import (
    ResumeController,
    ResumeLedger,
    ResumePhase,
    rehydrate_git_meta_map,
)
from repowise.core.pipeline.resume.rehydrate import stored_git_meta_for


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


async def _jobs_by_phase(sf, repo_id: str) -> dict[str, list]:
    async with get_session(sf) as session:
        jobs = await SqlJobStore(session).list_jobs(repository_id=repo_id)
    grouped: dict[str, list] = {}
    for job in jobs:
        grouped.setdefault(job.phase, []).append(job)
    return grouped


async def test_ledger_marks_a_dead_runs_open_job_failed(sf):
    """A run killed mid-phase leaves its row RUNNING; the next run closes it."""
    repo_id = await _make_repo(sf)
    await ResumeLedger(sf, repo_id).mark_started(ResumePhase.ANALYSIS)

    await ResumeLedger(sf, repo_id, reap_abandoned=True).mark_started(ResumePhase.INDEX)

    jobs = await _jobs_by_phase(sf, repo_id)
    [abandoned] = jobs["analysis"]
    assert abandoned.state == JobState.FAILED
    assert abandoned.error == ABANDONED_JOB_ERROR
    [current] = jobs["index"]
    assert current.state == JobState.RUNNING


async def test_ledger_without_the_init_lock_leaves_open_jobs_alone(sf):
    """An overlapping init that lost the lock must not fail the owner's live row."""
    repo_id = await _make_repo(sf)
    await ResumeLedger(sf, repo_id, reap_abandoned=True).mark_started(ResumePhase.ANALYSIS)

    await ResumeLedger(sf, repo_id).mark_started(ResumePhase.INDEX)

    jobs = await _jobs_by_phase(sf, repo_id)
    assert [job.state for job in jobs["analysis"]] == [JobState.RUNNING]
    assert [job.state for job in jobs["index"]] == [JobState.RUNNING]


async def test_ledger_does_not_reap_its_own_open_jobs(sf):
    repo_id = await _make_repo(sf)
    ledger = ResumeLedger(sf, repo_id, reap_abandoned=True)
    await ledger.mark_started(ResumePhase.INDEX)

    await ledger.mark_started(ResumePhase.ANALYSIS)

    jobs = await _jobs_by_phase(sf, repo_id)
    assert [job.state for job in jobs["index"]] == [JobState.RUNNING]
    assert [job.state for job in jobs["analysis"]] == [JobState.RUNNING]


async def test_ledger_ignores_open_jobs_of_other_phases(sf):
    """The git backfill runs under its own lock; init must not close its row."""
    repo_id = await _make_repo(sf)
    async with get_session(sf) as session:
        store = SqlJobStore(session)
        backfill = await store.create_job(repository_id=repo_id, phase="git.backfill")
        await store.update_state(backfill.id, JobState.RUNNING)

    await ResumeLedger(sf, repo_id, reap_abandoned=True).mark_started(ResumePhase.INDEX)

    jobs = await _jobs_by_phase(sf, repo_id)
    assert [job.state for job in jobs["git.backfill"]] == [JobState.RUNNING]


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


async def test_controller_skips_analysis_when_index_and_analysis_complete(sf):
    repo_id = await _make_repo(sf)
    ledger = ResumeLedger(sf, repo_id)
    await ledger.mark_completed(ResumePhase.INDEX)
    await ledger.mark_completed(ResumePhase.ANALYSIS)

    ctrl = ResumeController(sf, repo_id, resume=True)
    assert await ctrl.can_skip(ResumePhase.ANALYSIS) is True


# ---------------------------------------------------------------------------
# Analysis rehydration (generation input on a resumed run)
# ---------------------------------------------------------------------------


async def test_rehydrate_dead_code_report_maps_generation_fields(sf):
    from repowise.core.persistence.models import DeadCodeFinding
    from repowise.core.pipeline.resume.rehydrate import rehydrate_dead_code_report

    repo_id = await _make_repo(sf)
    async with get_session(sf) as session:
        session.add(
            DeadCodeFinding(
                repository_id=repo_id,
                kind="unused_export",
                file_path="pkg/a.py",
                symbol_name="foo",
                symbol_kind="function",
                confidence=0.9,
                reason="never imported",
                safe_to_delete=True,
                status="open",
            )
        )

    async with get_session(sf) as session:
        report = await rehydrate_dead_code_report(session, repo_id)

    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.file_path == "pkg/a.py"
    assert f.symbol_name == "foo"
    assert f.kind == "unused_export"
    assert f.safe_to_delete is True


async def test_rehydrate_decision_report_parses_affected_files(sf):
    from repowise.core.persistence.models import DecisionRecord
    from repowise.core.pipeline.resume.rehydrate import rehydrate_decision_report

    repo_id = await _make_repo(sf)
    async with get_session(sf) as session:
        session.add(
            DecisionRecord(
                repository_id=repo_id,
                title="Adopt X",
                decision="use X",
                rationale="it is fast",
                source="inline_marker",
                confidence=0.8,
                evidence_file="x.py",
                affected_files_json='["x.py", "y.py"]',
            )
        )

    async with get_session(sf) as session:
        report = await rehydrate_decision_report(session, repo_id)

    assert len(report.decisions) == 1
    d = report.decisions[0]
    assert d.title == "Adopt X"
    assert d.affected_files == ["x.py", "y.py"]


async def test_rehydrate_analysis_returns_empty_views_when_none_persisted(sf):
    """A completed-but-empty analysis rehydrates to empty reports, not an
    error — the caller trusts the ledger and does not recompute."""
    repo_id = await _make_repo(sf)
    ctrl = ResumeController(sf, repo_id, resume=True)

    dead_code_report, decision_report = await ctrl.rehydrate_analysis()
    assert dead_code_report.findings == []
    assert decision_report.decisions == []


# ---------------------------------------------------------------------------
# A failed checkpoint must be audible
# ---------------------------------------------------------------------------


class _RecordingProgress:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.phases: list[tuple[str, str]] = []

    def on_message(self, level: str, text: str) -> None:
        self.messages.append((level, text))

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self.phases.append(("start", phase))

    def on_phase_done(self, phase: str) -> None:
        self.phases.append(("done", phase))


async def test_a_failed_index_checkpoint_is_reported(sf, monkeypatch):
    """The CLI promises "indexed work so far has been saved — run --resume".

    When this write fails that promise is false. It was reported only through
    ``logger.warning``, which the CLI pins to ERROR, so the user found out by
    paying for the whole index a second time.
    """
    repo_id = await _make_repo(sf)

    def _boom(*_a: object, **_k: object):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(
        "repowise.core.pipeline.resume.controller.persist_ingestion", _boom
    )

    progress = _RecordingProgress()
    ctrl = ResumeController(sf, repo_id, resume=False)
    await ctrl.checkpoint_index(
        parsed_files=[],
        graph_builder=None,
        git_metadata_list=[],
        progress=progress,
    )

    assert progress.messages, "a failed checkpoint said nothing"
    level, text = progress.messages[0]
    assert level == "warning"
    assert "database is locked" in text
    # A failed write must not leave a done row behind.
    assert ("done", "checkpoint.index") not in progress.phases
    # And it must not claim the index is on disk when it is not.
    assert await ctrl.can_skip(ResumePhase.INDEX) is False


async def test_a_successful_checkpoint_says_nothing(sf, monkeypatch):
    """Only failures are worth a line; a clean run must stay quiet.

    The persisters are stubbed to succeed rather than fed a real graph: what
    is under test is that the reporting sits on the failure path only, not
    what ``persist_ingestion`` writes.
    """
    repo_id = await _make_repo(sf)

    async def _ok(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_ingestion", _ok)
    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_git", _ok)

    progress = _RecordingProgress()

    ctrl = ResumeController(sf, repo_id, resume=False)
    await ctrl.checkpoint_index(
        parsed_files=[],
        graph_builder=None,
        git_metadata_list=[],
        progress=progress,
    )

    assert progress.messages == []


async def test_index_checkpoint_is_timed(sf, monkeypatch):
    """The checkpoint write times itself and shows as its own step."""
    from repowise.core.pipeline.phase_timing import PhaseTimingRecorder

    repo_id = await _make_repo(sf)

    async def _ok(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_ingestion", _ok)
    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_git", _ok)

    recorder = PhaseTimingRecorder(_RecordingProgress())
    await ResumeController(sf, repo_id, resume=False).checkpoint_index(
        parsed_files=[],
        graph_builder=None,
        git_metadata_list=[],
        progress=recorder,
    )

    assert "persist.checkpoint" in recorder.timings
    assert recorder._inner.phases == [("start", "checkpoint.index"), ("done", "checkpoint.index")]


async def test_index_checkpoint_times_the_scan_and_the_change_rebuild(sf, monkeypatch):
    """The security scan and the git walk get their own keys inside the checkpoint.

    They ran under ``persist.checkpoint`` with no name, so a 12 s "saving" row
    was mostly a scan and a history walk. The real persisters run here; only
    the heavy bodies are stubbed.
    """
    from types import SimpleNamespace

    import networkx as nx

    from repowise.core.pipeline.phase_timing import PhaseTimingRecorder

    repo_id = await _make_repo(sf)
    scanned: list[bool] = []
    rebuilt: list[bool] = []

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    async def _scan(*_a: object, **_k: object) -> None:
        scanned.append(True)

    async def _rebuild(*_a: object, **_k: object) -> None:
        rebuilt.append(True)

    monkeypatch.setattr("repowise.core.pipeline.persist.persist_graph_nodes", _noop)
    monkeypatch.setattr("repowise.core.pipeline.persist.persist_security_findings", _scan)
    monkeypatch.setattr("repowise.core.pipeline.persist.git._rebuild_changes", _rebuild)

    recorder = PhaseTimingRecorder(_RecordingProgress())
    await ResumeController(sf, repo_id, resume=False).checkpoint_index(
        parsed_files=[],
        graph_builder=SimpleNamespace(graph=nx.DiGraph),
        git_metadata_list=[],
        git_summary=SimpleNamespace(),
        progress=recorder,
    )

    assert scanned and rebuilt, "the checkpoint no longer ran the scan or the rebuild"
    assert "persist.security_scan" in recorder.timings
    assert "persist.changes" in recorder.timings
    assert "persist.checkpoint" in recorder.timings


async def test_security_scan_runs_off_the_event_loop(monkeypatch):
    """``scan_source_map`` is CPU-bound; it must not block the loop thread."""
    import threading
    from types import SimpleNamespace

    from repowise.core.pipeline.persist.security import persist_security_findings

    seen: dict[str, int] = {}

    def _scan(parsed_files, source_map):
        seen["thread"] = threading.get_ident()
        return {}, []

    class _Scanner:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        async def replace_findings(self, *_a: object, **_k: object) -> None:
            return None

    monkeypatch.setattr("repowise.core.analysis.security_scan.scan_source_map", _scan)
    monkeypatch.setattr("repowise.core.analysis.security_scan.SecurityScanner", _Scanner)

    await persist_security_findings(
        SimpleNamespace(parsed_files=[], source_map={}), object(), "repo-1"
    )

    assert seen["thread"] != threading.get_ident()


async def test_failed_symbol_analysis_reconciliation_keeps_analysis_retryable(sf, monkeypatch):
    """ANALYSIS cannot complete while post-health symbol state is stale."""
    repo_id = await _make_repo(sf)

    async def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("symbol write failed")

    async def _ok(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_symbol_analysis", _boom)
    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_analysis", _ok)

    progress = _RecordingProgress()
    ctrl = ResumeController(sf, repo_id, resume=False)
    await ctrl.checkpoint_analysis(
        parsed_files=[],
        dead_code_report=None,
        health_report=None,
        decision_report=None,
        git_metadata_list=[],
        progress=progress,
    )

    assert ResumePhase.ANALYSIS not in await ResumeLedger(sf, repo_id).completed_phases()
    assert progress.messages == [
        (
            "warning",
            "Analysis checkpoint not saved (symbol write failed); "
            "a resumed run will have to recompute dead code, health and decisions.",
        )
    ]


async def test_decision_extraction_completion_tracking(sf, monkeypatch):
    """Test that ResumeController correctly records and checks decision extraction status."""
    from types import SimpleNamespace

    async def _ok(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_symbol_analysis", _ok)
    monkeypatch.setattr("repowise.core.pipeline.resume.controller.persist_analysis", _ok)

    repo_id = await _make_repo(sf)
    progress = _RecordingProgress()

    # Case 1: checkpoint_analysis without decision_report (e.g. extraction not completed)
    ctrl1 = ResumeController(sf, repo_id, resume=False)
    await ctrl1.checkpoint_analysis(
        parsed_files=[],
        dead_code_report=None,
        health_report=None,
        decision_report=None,
        git_metadata_list=[],
        progress=progress,
    )

    assert await ctrl1.has_completed_decision_extraction() is False

    # Mark decision extraction completed
    await ctrl1.mark_decision_extraction_completed(count=5)
    assert await ctrl1.has_completed_decision_extraction() is True

    # Case 2: fresh controller with genuine empty decisions
    repo_id_2 = await _make_repo(sf)
    ctrl2 = ResumeController(sf, repo_id_2, resume=False)
    empty_decision_report = SimpleNamespace(decisions=[])
    await ctrl2.checkpoint_analysis(
        parsed_files=[],
        dead_code_report=None,
        health_report=None,
        decision_report=empty_decision_report,
        git_metadata_list=[],
        progress=progress,
    )

    assert await ctrl2.has_completed_decision_extraction() is True


async def test_stored_git_meta_for_reads_only_the_paths(sf):
    repo_id = await _make_repo(sf)
    async with get_session(sf) as session:
        await upsert_git_metadata_bulk(
            session,
            repo_id,
            [{"file_path": "src/a.py"}, {"file_path": "src/b.py"}],
        )

    async with get_session(sf) as session:
        meta = await stored_git_meta_for(session, repo_id, ["src/b.py", "src/gone.py"])

    assert set(meta) == {"src/b.py"}


def test_only_the_init_holding_the_lock_owns_it(tmp_path):
    from repowise.core.update_lock import holds_init_lock, init_in_progress

    (tmp_path / ".repowise").mkdir()
    assert not holds_init_lock(tmp_path)
    with init_in_progress(tmp_path):
        assert holds_init_lock(tmp_path)
    assert not holds_init_lock(tmp_path)
