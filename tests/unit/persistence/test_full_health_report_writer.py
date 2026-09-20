"""The writer every full re-score goes through, and what it must rebuild.

A re-score deletes and re-inserts every finding in the repository. The
materialized queues are derived from those findings, so unless they are
rebuilt in the same transaction they are left describing rows that no longer
exist. These pin that they move together, and that the metric rows carry the
commit the scores were computed against.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence.models import (
    HealthFileMetric,
    PerformanceOpportunity,
)
from repowise.core.pipeline.persist import save_full_health_report
from tests.unit.persistence.helpers import insert_repo


def _perf_finding(
    line: int, *, path: str = "src/app.py", sink: str = "src/db.py::fetch"
) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="serial_await_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [f"{path}::run", "src/shared.py::load", sink],
            "resolution_basis": "call-site",
            "dataflow_verified": True,
        },
        health_impact=0.0,
        reason="Awaited database work repeats for every loop iteration.",
        dimension="performance",
    )


def _report(findings: list, *, metrics: list | None = None) -> SimpleNamespace:
    link_performance_findings(findings)
    return SimpleNamespace(
        metrics=metrics if metrics is not None else [_metric()],
        findings=findings,
        refactoring_suggestions=[],
        performance_plan_policy=None,
    )


def _metric(path: str = "src/app.py") -> dict:
    return {
        "file_path": path,
        "score": 5.0,
        "max_ccn": 3,
        "max_nesting": 1,
        "nloc": 40,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": "src",
    }


async def _open_perf_ids(session, repo_id: str) -> set[str]:
    rows = (
        (
            await session.execute(
                select(PerformanceOpportunity).where(
                    PerformanceOpportunity.repository_id == repo_id,
                    PerformanceOpportunity.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.opportunity_id for row in rows}


class TestSaveFullHealthReport:
    async def test_it_materializes_the_queue_from_the_findings_it_wrote(self, async_session):
        repo_id = (await insert_repo(async_session)).id
        await save_full_health_report(
            async_session, repo_id, _report([_perf_finding(10)]), analyzed_commit="a" * 40
        )
        assert len(await _open_perf_ids(async_session, repo_id)) == 1

    async def test_a_cause_the_rescore_no_longer_sees_is_resolved(self, async_session):
        """The defect this exists for: a re-score replaces every finding.

        Without the rebuild in the same transaction the queue keeps serving the
        opportunity, pointing at a finding row the re-score deleted.
        """
        repo_id = (await insert_repo(async_session)).id
        await save_full_health_report(
            async_session, repo_id, _report([_perf_finding(10)]), analyzed_commit="a" * 40
        )
        before = await _open_perf_ids(async_session, repo_id)
        assert len(before) == 1

        # The repeated work now lands on a different sink, which is a
        # different cause. The re-score deleted the finding the first one was
        # built from, so the queue must stop serving it.
        await save_full_health_report(
            async_session,
            repo_id,
            _report(
                [_perf_finding(10, path="src/other.py", sink="src/cache.py::lookup")],
                metrics=[_metric("src/other.py")],
            ),
            analyzed_commit="b" * 40,
        )
        after = await _open_perf_ids(async_session, repo_id)
        assert len(after) == 1
        assert after != before

        stored = {row.opportunity_id: row.status for row in await session_rows(async_session, repo_id)}
        assert stored[next(iter(before))] == "resolved"
        assert stored[next(iter(after))] == "open"

    async def test_it_stamps_the_metric_rows_with_the_analyzed_commit(self, async_session):
        repo_id = (await insert_repo(async_session)).id
        await save_full_health_report(
            async_session, repo_id, _report([]), analyzed_commit="c" * 40
        )
        rows = (
            (
                await async_session.execute(
                    select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        assert rows
        assert {row.analyzed_commit for row in rows} == {"c" * 40}

    async def test_a_pass_that_scored_nothing_writes_nothing(self, async_session):
        """A parse failure must not read as "this repository became clean".

        Every write here replaces the repository's rows wholesale, so honouring
        an empty report would delete the metrics, resolve plans a person had
        triaged, and empty both queues on the strength of a broken run.
        """
        repo_id = (await insert_repo(async_session)).id
        await save_full_health_report(
            async_session, repo_id, _report([_perf_finding(10)]), analyzed_commit="a" * 40
        )
        before = await _open_perf_ids(async_session, repo_id)
        assert before

        empty = SimpleNamespace(
            metrics=[], findings=[], refactoring_suggestions=[], performance_plan_policy=None
        )
        await save_full_health_report(
            async_session, repo_id, empty, analyzed_commit="b" * 40
        )

        assert await _open_perf_ids(async_session, repo_id) == before
        metrics = (
            (
                await async_session.execute(
                    select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        assert metrics, "an empty report must not delete the stored metrics"

    async def test_an_unstamped_pass_leaves_the_column_null(self, async_session):
        """``None`` reads as "not recorded"; it must not borrow a stale sha."""
        repo_id = (await insert_repo(async_session)).id
        await save_full_health_report(async_session, repo_id, _report([]), analyzed_commit=None)
        rows = (
            (
                await async_session.execute(
                    select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
                )
            )
            .scalars()
            .all()
        )
        assert {row.analyzed_commit for row in rows} == {None}


async def session_rows(session, repo_id: str):
    return (
        (
            await session.execute(
                select(PerformanceOpportunity).where(
                    PerformanceOpportunity.repository_id == repo_id
                )
            )
        )
        .scalars()
        .all()
    )
