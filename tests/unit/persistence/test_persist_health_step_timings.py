"""``persist.health`` names its repository-wide steps in the run's timings.

The span alone cannot say whether an update pays for the plan finalizers, the
history refresh or the snapshot, and only real stores show which one grows.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.models import HealthFileMetric
from repowise.core.pipeline.incremental import persist_partial_health
from repowise.core.pipeline.phase_timing import PhaseTimings


def _report(**overrides):
    base = dict(
        metrics=[],
        findings=[],
        refactoring_suggestions=[],
        function_blame_rows=[],
        authoritative_paths={"a.py"},
        performance_authoritative_paths=set(),
        performance_plan_policy=None,
    )
    return SimpleNamespace(**{**base, **overrides})


async def _repo(session) -> str:
    repo = await upsert_repository(session, name="r", local_path="/tmp/r")
    session.add(HealthFileMetric(repository_id=repo.id, file_path="a.py", score=7.0, nloc=10))
    await session.flush()
    return repo.id


async def test_each_repository_wide_step_is_timed(async_session, tmp_path) -> None:
    repo_id = await _repo(async_session)
    timings = PhaseTimings()

    await persist_partial_health(async_session, repo_id, _report(), tmp_path, timings=timings)

    assert {
        "persist.health.hotness",
        "persist.health.finalize_performance",
        "persist.health.finalize_refactoring",
        "persist.health.repairs",
        "persist.health.history",
        "persist.health.snapshot",
    } <= set(timings.totals)


async def test_a_deletion_only_update_times_the_hotness_refresh(async_session) -> None:
    repo_id = await _repo(async_session)
    timings = PhaseTimings()

    await persist_partial_health(
        async_session, repo_id, _report(authoritative_paths=set()), timings=timings
    )

    assert set(timings.totals) == {"persist.health.hotness"}


async def test_no_table_still_persists(async_session) -> None:
    """Callers that keep no timings (the CLI wrapper, tests) are unaffected."""
    repo_id = await _repo(async_session)

    await persist_partial_health(async_session, repo_id, _report())


async def test_the_update_hands_its_timings_to_the_health_writer(monkeypatch) -> None:
    from repowise.core.pipeline.incremental import persist_index

    seen: dict = {}

    async def _fake(*args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(persist_index, "persist_partial_health", _fake)
    ctx = SimpleNamespace(
        session=object(),
        repo_id="r",
        partial_health_report=object(),
        repo_path="/tmp/r",
        timings=PhaseTimings(),
    )

    await persist_index.persist_health(ctx)

    assert seen["timings"] is ctx.timings
    assert "persist.health" in ctx.timings.totals
