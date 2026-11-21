"""``finalize_performance_opportunities`` reads stored query counts onto database causes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from repowise.core.persistence.crud import finalize_performance_opportunities
from repowise.core.persistence.models import (
    EvidenceQueryCount,
    EvidenceRun,
    HealthFinding,
    PerformanceOpportunity,
)
from tests.unit.persistence.helpers import insert_repo

OWNER = "src/api.py::list_orders"


def _run(
    repo_id: str, run_id: str, day: int, *, name: str = "api-tests", partial: bool = False
) -> EvidenceRun:
    return EvidenceRun(
        id=run_id, repository_id=repo_id, run_key=run_id, kind="query_count",
        format="repowise-evidence-v1", source="cli", workload_name=name,
        mapping_partial=partial,
        workload_class="unit_test", unit="count", commit_sha="c" * 40,
        measured_at=datetime(2026, 11, day, tzinfo=UTC),
    )


def _count(repo_id: str, run_id: str, scope: str, items: int | None, count: int):
    return EvidenceQueryCount(
        run_id=run_id, repository_id=repo_id, scope_kind="test", scope_id=scope,
        site_symbol_id=OWNER, statement_fp="9f2c1a0b", scope_items=items, query_count=count,
    )


async def test_the_newest_run_decides_and_lands_on_the_cause(async_session) -> None:
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            HealthFinding(
                repository_id=repo.id, file_path="src/api.py", biomarker_type="io_in_loop",
                severity="medium", line_start=5, line_end=5, dimension="performance",
                status="open", function_name="list_orders",
                details_json=json.dumps(
                    {"boundary_kind": "db", "loop_magnitude": "grows_with_data",
                     "role_owner": OWNER, "execution_role": "request"}
                ),
            ),
            # An older run of the same workload said the count was flat: superseded.
            _run(repo.id, "old", 14),
            _count(repo.id, "old", "t_small", 10, 2),
            _count(repo.id, "old", "t_big", 100, 2),
            _run(repo.id, "new", 15),
            _count(repo.id, "new", "t_small", 10, 11),
            _count(repo.id, "new", "t_big", 100, 101),
            # A newer run that mapped only part of its entries never decides.
            _run(repo.id, "partial", 16, partial=True),
            _count(repo.id, "partial", "t_small", 10, 2),
            _count(repo.id, "partial", "t_big", 100, 2),
        ]
    )
    await async_session.flush()

    await finalize_performance_opportunities(async_session, repo.id)

    row = (await async_session.execute(select(PerformanceOpportunity))).scalar_one()
    facets = json.loads(row.details_json)["facets"]
    assert (facets["queries"], facets["query_statements"], facets["query_items"]) == (
        "grows",
        "101",
        "100",
    )


async def test_sizes_from_two_workloads_are_never_compared(async_session) -> None:
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            HealthFinding(
                repository_id=repo.id, file_path="src/api.py", biomarker_type="io_in_loop",
                severity="medium", line_start=5, line_end=5, dimension="performance",
                status="open",
                details_json=json.dumps({"boundary_kind": "db", "role_owner": OWNER}),
            ),
            _run(repo.id, "a", 15, name="small"),
            _count(repo.id, "a", "t", 10, 2),
            _run(repo.id, "b", 15, name="big"),
            _count(repo.id, "b", "t", 100, 2),
        ]
    )
    await async_session.flush()

    await finalize_performance_opportunities(async_session, repo.id)

    row = (await async_session.execute(select(PerformanceOpportunity))).scalar_one()
    assert json.loads(row.details_json)["facets"]["queries"] == "ran"


async def test_no_query_runs_add_nothing(async_session) -> None:
    repo = await insert_repo(async_session)
    async_session.add(
        HealthFinding(
            repository_id=repo.id, file_path="src/api.py", biomarker_type="io_in_loop",
            severity="medium", line_start=5, line_end=5, dimension="performance",
            status="open", details_json=json.dumps({"boundary_kind": "db", "role_owner": OWNER}),
        )
    )
    await async_session.flush()

    await finalize_performance_opportunities(async_session, repo.id)

    row = (await async_session.execute(select(PerformanceOpportunity))).scalar_one()
    assert "queries" not in json.loads(row.details_json)["facets"]
