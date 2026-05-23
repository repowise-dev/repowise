"""Unit tests for pipeline checkpoint/resume.

Exercise the real ``pipeline_hooks`` → queue → ``JobStore`` seam with a fresh
registry (no global-state leakage) and an in-memory fake JobStore.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence._interfaces.job_store import JobRecord, JobState
from repowise.core.pipeline.checkpoint import (
    DEFAULT_CHECKPOINT_PHASES,
    PhaseCheckpointer,
    find_completed_phases,
)
from repowise.core.registry import HookPhase, HookProgressCallback, PipelineHookRegistry


class FakeJobStore:
    """In-memory JobStore tracking create/update calls for assertions."""

    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.events: list[tuple[str, str]] = []  # (op, phase-or-state)
        self._seq = 0

    async def create_job(self, *, repository_id, phase, metadata=None) -> JobRecord:
        self._seq += 1
        jid = f"job{self._seq}"
        now = datetime.now(UTC)
        rec = JobRecord(
            jid, repository_id, phase, JobState.PENDING, None, now, now, None, metadata or {}
        )
        self.jobs[jid] = rec
        self.events.append(("create", phase))
        return rec

    async def update_state(self, job_id, state, *, cursor=None, error=None) -> JobRecord:
        old = self.jobs[job_id]
        rec = JobRecord(
            old.id, old.repository_id, old.phase, state, cursor or old.cursor,
            old.started_at, datetime.now(UTC), error, old.metadata,
        )
        self.jobs[job_id] = rec
        self.events.append(("state", f"{old.phase}:{state.value}"))
        return rec

    async def list_jobs(self, *, repository_id=None, phase=None, state=None, limit=100):
        return [
            j
            for j in self.jobs.values()
            if (repository_id is None or j.repository_id == repository_id)
        ]


async def _fire_phase(cb: HookProgressCallback, phase: str) -> None:
    cb.on_phase_start(phase, None)
    cb.on_phase_done(phase)


async def test_checkpointer_records_phase_lifecycle() -> None:
    registry = PipelineHookRegistry()
    store = FakeJobStore()
    cp = PhaseCheckpointer(store, "repo-1", phases=("parse", "git"), registry=registry)
    cb = HookProgressCallback(None, registry)

    async with cp:
        await _fire_phase(cb, "parse")
        await _fire_phase(cb, "git")

    # Two jobs created (parse, git), each RUNNING then COMPLETED, in order.
    assert store.events == [
        ("create", "parse"),
        ("state", "parse:running"),
        ("state", "parse:completed"),
        ("create", "git"),
        ("state", "git:running"),
        ("state", "git:completed"),
    ]


async def test_checkpointer_ignores_untracked_phases() -> None:
    registry = PipelineHookRegistry()
    store = FakeJobStore()
    cp = PhaseCheckpointer(store, "repo-1", phases=("parse",), registry=registry)
    cb = HookProgressCallback(None, registry)

    async with cp:
        await _fire_phase(cb, "external_systems")  # not tracked
        await _fire_phase(cb, "parse")

    assert [e for e in store.events if e[0] == "create"] == [("create", "parse")]


async def test_checkpointer_unregisters_hooks_on_close() -> None:
    registry = PipelineHookRegistry()
    store = FakeJobStore()
    cp = PhaseCheckpointer(store, "repo-1", phases=("parse",), registry=registry)

    async with cp:
        assert registry.hooks_for("parse", HookPhase.PRE)
    # After close, the checkpointer's hooks are gone — no leakage into the
    # shared registry for the next run.
    assert not registry.hooks_for("parse", HookPhase.PRE)
    assert not registry.hooks_for("parse", HookPhase.POST)


async def test_find_completed_phases() -> None:
    store = FakeJobStore()
    j1 = await store.create_job(repository_id="r", phase="parse")
    await store.update_state(j1.id, JobState.COMPLETED)
    j2 = await store.create_job(repository_id="r", phase="git")
    await store.update_state(j2.id, JobState.RUNNING)

    done = await find_completed_phases(store, "r")
    assert done == {"parse"}


def test_default_phases_are_sane() -> None:
    assert "parse" in DEFAULT_CHECKPOINT_PHASES
    assert "generation" in DEFAULT_CHECKPOINT_PHASES


@pytest.mark.parametrize("phase", DEFAULT_CHECKPOINT_PHASES)
def test_default_phase_names_are_strings(phase: str) -> None:
    assert isinstance(phase, str) and phase
