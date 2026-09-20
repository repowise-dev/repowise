"""The writer that turns stored performance findings into a servable queue.

Grouping used to happen on every read, so a page of twenty cost the whole
repository. It happens here instead, once per health persistence transaction,
and both index paths call it. These tests pin what that means: the same answer
from a full and a partial run, plans generated exactly once, a cause that stops
being observed resolved rather than dropped, and a schema that upgrades and
rolls back.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import (
    PERFORMANCE_MODEL_VERSION,
    link_performance_findings,
)
from repowise.core.analysis.health.refactoring.performance_fix import PerformancePlanPolicy
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    HealthFinding,
    PerformanceOpportunity,
    PerformanceSummary,
    RefactoringSuggestion,
)
from tests.unit.persistence.helpers import insert_repo

_SHARED = ["{caller}::run", "src/shared.py::load", "src/db.py::fetch"]


def _finding(path: str, line: int, *, marker: str = "serial_await_in_loop", **details):
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [node.format(caller=path) for node in _SHARED],
            "resolution_basis": "call-site",
            "dataflow_verified": True,
            **details,
        },
        health_impact=0.0,
        reason="Awaited database work repeats for every loop iteration.",
        dimension="performance",
    )


def _corpus() -> list:
    return [_finding("src/a.py", 10), _finding("src/b.py", 20), _finding("src/c.py", 30)]


async def _write(session, repo_id: str, findings: list, **kwargs) -> int:
    link_performance_findings(findings)
    await crud.save_health_findings(session, repo_id, findings)
    return await crud.finalize_performance_opportunities(session, repo_id, **kwargs)


async def _rows(session, repo_id: str) -> list[PerformanceOpportunity]:
    result = await session.execute(
        select(PerformanceOpportunity)
        .where(PerformanceOpportunity.repository_id == repo_id)
        .order_by(PerformanceOpportunity.rank_position)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_one_pass_materializes_the_queue_its_plans_and_its_headline(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    total = await _write(async_session, repo.id, _corpus(), analyzed_commit="b" * 40)

    assert total == 1
    (row,) = await _rows(async_session, repo.id)
    assert row.status == "open"
    assert row.rank_position == 0
    assert row.observations_total == 3
    assert row.affected_call_sites_total == 3
    assert row.intervention_symbol == "src/shared.py::load"
    assert row.file_path == "src/shared.py"
    assert row.plan_state == "available"
    assert row.performance_model_version == PERFORMANCE_MODEL_VERSION
    assert row.analyzed_commit == "b" * 40

    # Filter and order live in columns; the rest stays in the open payload, and
    # nothing appears in both.
    details = json.loads(row.details_json)
    assert set(details) & {"execution_context", "rank_score", "boundary_kind"} == set()
    assert details["facets"]["leverage"] == "local"

    plans = await crud.get_refactoring_suggestions(
        async_session, repo.id, refactoring_type="performance_fix"
    )
    assert [plan.opportunity_id for plan in plans] == [row.opportunity_id]

    summary = await crud.get_performance_summary(async_session, repo.id)
    assert summary.opportunities_total == 1
    assert json.loads(summary.summary_json)["lead"]["opportunity_id"] == row.opportunity_id


@pytest.mark.asyncio
async def test_every_stored_observation_carries_its_cause_and_its_public_id(
    async_session,
) -> None:
    """Both are columns, which is what makes drill-down an indexed seek."""
    repo = await insert_repo(async_session)
    await _write(async_session, repo.id, _corpus())
    rows = (
        (
            await async_session.execute(
                select(HealthFinding).where(HealthFinding.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    (opportunity,) = await _rows(async_session, repo.id)
    assert {row.opportunity_id for row in rows} == {opportunity.opportunity_id}
    assert all(row.public_id and row.public_id.startswith("finding_") for row in rows)
    assert len({row.public_id for row in rows}) == 3


@pytest.mark.asyncio
async def test_a_store_written_before_the_columns_existed_is_backfilled(
    async_session,
) -> None:
    """The columns are nullable, so an upgraded store arrives with them empty."""
    repo = await insert_repo(async_session)
    await _write(async_session, repo.id, _corpus())
    await async_session.execute(
        HealthFinding.__table__.update()
        .where(HealthFinding.repository_id == repo.id)
        .values(public_id=None, opportunity_id=None)
    )
    await crud.finalize_performance_opportunities(async_session, repo.id)

    rows = (
        (
            await async_session.execute(
                select(HealthFinding).where(HealthFinding.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(row.public_id and row.opportunity_id for row in rows)


@pytest.mark.asyncio
async def test_a_partial_run_and_a_full_run_reach_the_same_queue(async_session) -> None:
    """The partial path sees a subset, so it must not build the queue itself.

    It writes its own findings and then rebuilds from the merged stored set,
    which is the only way the two paths can agree.
    """
    full_repo = await insert_repo(async_session, name="full", local_path="/tmp/full")
    await _write(async_session, full_repo.id, _corpus())
    expected = [
        (row.opportunity_id, row.rank_position, row.observations_total, row.plan_state)
        for row in await _rows(async_session, full_repo.id)
    ]

    partial_repo = await insert_repo(async_session, name="partial", local_path="/tmp/partial")
    first = _corpus()[:1]
    link_performance_findings(first)
    await crud.save_health_findings(async_session, partial_repo.id, first)
    rest = _corpus()[1:]
    link_performance_findings(rest)
    await crud.upsert_health_findings(
        async_session,
        partial_repo.id,
        rest,
        file_paths=["src/b.py", "src/c.py"],
    )
    await crud.finalize_performance_opportunities(async_session, partial_repo.id)

    assert [
        (row.opportunity_id, row.rank_position, row.observations_total, row.plan_state)
        for row in await _rows(async_session, partial_repo.id)
    ] == expected


@pytest.mark.asyncio
async def test_a_cause_nobody_observes_is_resolved_and_its_plan_withdrawn(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    await _write(async_session, repo.id, _corpus())
    (before,) = await _rows(async_session, repo.id)

    await _write(async_session, repo.id, [_finding("src/z.py", 40, marker="io_in_loop")])
    rows = {row.opportunity_id: row for row in await _rows(async_session, repo.id)}

    assert rows[before.opportunity_id].status == "resolved"
    assert len([row for row in rows.values() if row.status == "open"]) == 1
    # The withdrawn cause's plan goes with it: plans are replaced wholesale
    # from the current opportunities, never left behind pointing at nothing.
    plans = await crud.get_refactoring_suggestions(
        async_session, repo.id, refactoring_type="performance_fix"
    )
    assert before.opportunity_id not in {plan.opportunity_id for plan in plans}


@pytest.mark.asyncio
async def test_a_row_from_an_older_model_is_resolved_rather_than_reused(
    async_session,
) -> None:
    """Two model versions disagree about membership, so neither can stand in
    for the other. The old row stays addressable and stops reading as current.
    """
    repo = await insert_repo(async_session)
    await _write(async_session, repo.id, _corpus())
    async_session.add(
        PerformanceOpportunity(
            id="0" * 32,
            repository_id=repo.id,
            opportunity_id="perf1_0123456789abcdef0123",
            performance_model_version=PERFORMANCE_MODEL_VERSION - 1,
            status="open",
        )
    )
    await async_session.flush()

    await crud.finalize_performance_opportunities(async_session, repo.id)
    rows = {row.opportunity_id: row.status for row in await _rows(async_session, repo.id)}
    assert rows["perf1_0123456789abcdef0123"] == "resolved"


@pytest.mark.asyncio
async def test_disabling_plans_leaves_the_queue_and_withdraws_the_plans(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    await _write(
        async_session,
        repo.id,
        _corpus(),
        plan_policy=PerformancePlanPolicy(enabled=False),
    )
    (row,) = await _rows(async_session, repo.id)
    assert row.plan_state == "not_persisted"
    assert row.fix_strategy == "parallelize_independent_awaits"
    assert (
        await async_session.execute(
            select(RefactoringSuggestion).where(
                RefactoringSuggestion.repository_id == repo.id
            )
        )
    ).scalars().first() is None


@pytest.mark.asyncio
async def test_a_repository_with_no_performance_findings_still_has_a_headline(
    async_session,
) -> None:
    """A missing summary row means "never analyzed"; an empty one means clear.

    Collapsing the two would make an index that has not run yet look healthy.
    """
    repo = await insert_repo(async_session)
    assert await crud.get_performance_summary(async_session, repo.id) is None

    await crud.finalize_performance_opportunities(async_session, repo.id)
    summary = await crud.get_performance_summary(async_session, repo.id)
    assert isinstance(summary, PerformanceSummary)
    assert summary.opportunities_total == 0
    assert json.loads(summary.summary_json)["lead"] is None


@pytest.mark.asyncio
async def test_a_rebuild_that_fails_halfway_leaves_the_previous_state(
    async_session, monkeypatch
) -> None:
    """The queue must never describe findings that were never stored.

    The incremental caller logs a failed health step and carries on to commit
    the rest of the run, so the savepoint around the write is what stops a
    half-finished rebuild from becoming the current answer.
    """
    from repowise.core.persistence.crud.analysis import performance as writer

    repo = await insert_repo(async_session)
    await _write(async_session, repo.id, _corpus(), analyzed_commit="1" * 40)
    (before,) = await _rows(async_session, repo.id)

    async def explode(*_args, **_kwargs):
        raise RuntimeError("summary write failed")

    monkeypatch.setattr(writer, "_write_summary", explode)
    findings = [_finding("src/z.py", 40, marker="io_in_loop")]
    link_performance_findings(findings)
    with pytest.raises(RuntimeError):
        async with async_session.begin_nested():
            await crud.save_health_findings(async_session, repo.id, findings)
            await crud.finalize_performance_opportunities(async_session, repo.id)

    rows = await _rows(async_session, repo.id)
    assert [(row.opportunity_id, row.status) for row in rows] == [
        (before.opportunity_id, "open")
    ]
    summary = await crud.get_performance_summary(async_session, repo.id)
    assert summary.analyzed_commit == "1" * 40
    stored = (
        (
            await async_session.execute(
                select(HealthFinding.file_path).where(
                    HealthFinding.repository_id == repo.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(stored) == ["src/a.py", "src/b.py", "src/c.py"]


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def test_the_migration_upgrades_and_rolls_back(tmp_path: Path) -> None:
    """Explicit migration for managed stores, both directions.

    Local stores are created by ``init_db`` and never see Alembic, which is why
    the model declaration and this migration have to describe the same shape.
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    db_path = tmp_path / "wiki.db"
    # Built without the ini file on purpose. Passing it makes ``env.py`` call
    # ``fileConfig``, which reconfigures logging for the rest of the session and
    # silences the warnings other tests assert on.
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    command.upgrade(config, "0057")
    assert {"performance_opportunities", "performance_summaries"} <= _tables(db_path)
    assert {"public_id", "opportunity_id"} <= _columns(db_path, "health_findings")
    assert "opportunity_id" in _columns(db_path, "refactoring_suggestions")

    command.downgrade(config, "0056")
    assert not {"performance_opportunities", "performance_summaries"} & _tables(db_path)
    assert not {"public_id", "opportunity_id"} & _columns(db_path, "health_findings")
    assert "opportunity_id" not in _columns(db_path, "refactoring_suggestions")
