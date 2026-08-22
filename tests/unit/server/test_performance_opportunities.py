"""Bounded causal performance product API and exact plan handoff."""

from __future__ import annotations

from httpx import AsyncClient

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import (
    build_performance_opportunities,
    link_performance_findings,
)
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)
from repowise.core.persistence import crud
from tests.unit.server.conftest import create_test_repo


def _finding(path: str, line: int, path_nodes: list[str], *, marker: str = "io_in_loop"):
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
            "path": path_nodes,
            "resolution_basis": "reliable-edge",
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )


async def _seed(app, client: AsyncClient) -> tuple[str, str]:
    repo = await create_test_repo(client)
    findings = [
        _finding("src/a.py", 10, ["src/a.py::run", "src/shared.py::load", "src/db.py::fetch"]),
        _finding("src/b.py", 20, ["src/b.py::run", "src/shared.py::load", "src/db.py::fetch"]),
        # A separate test-suite cause with no coherent shared intervention.
        _finding(
            "tests/test_db.py",
            30,
            ["tests/test_db.py::run", "src/db.py::fetch"],
            marker="serial_await_in_loop",
        ),
    ]
    link_performance_findings(findings)
    opportunities = build_performance_opportunities(findings)
    production = next(item for item in opportunities if item.execution_context == "production")
    plans = performance_fix_suggestions([production])
    async with app.state.session_factory() as session:
        await crud.save_health_findings(session, repo["id"], findings)
        await crud.save_refactoring_suggestions(session, repo["id"], plans)
        await session.commit()
    return repo["id"], production.opportunity_id


async def test_opportunities_are_grouped_bounded_and_split_by_context(
    client: AsyncClient, app
) -> None:
    repo_id, opportunity_id = await _seed(app, client)
    production = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities", params={"limit": 1}
        )
    ).json()

    assert len(production["items"]) == 1
    assert production["items"][0]["opportunity_id"] == opportunity_id
    assert production["items"][0]["affected_call_sites_total"] == 2
    assert production["items"][0]["confidence"] == "medium"
    assert production["items"][0]["plan_status"] == "available"
    assert production["items"][0]["plan_id"]
    assert production["summary"] == {
        "total": 2,
        "production_total": 1,
        "tooling_total": 0,
        "test_total": 1,
        "with_plan_total": 1,
        "without_plan_total": 1,
    }

    test_page = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities",
            params={"context": "test"},
        )
    ).json()
    assert test_page["total"] == 1
    assert test_page["items"][0]["plan_id"] is None
    assert test_page["items"][0]["plan_status"] == "no_safe_plan"


async def test_raw_evidence_is_paged_and_plan_matching_never_falls_back(
    client: AsyncClient, app
) -> None:
    repo_id, opportunity_id = await _seed(app, client)
    first = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings",
            params={"limit": 1},
        )
    ).json()
    second = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings",
            params={"limit": 1, "offset": first["next_offset"]},
        )
    ).json()
    assert first["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]

    async with app.state.session_factory() as session:
        rows = await crud.get_refactoring_suggestions(session, repo_id)
        rows[0].plan_json = '{"opportunity_id":"unrelated"}'
        await session.commit()
    page = (await client.get(f"/api/repos/{repo_id}/health/performance-opportunities")).json()
    assert page["items"][0]["plan_id"] is None
    assert page["items"][0]["plan_status"] == "not_persisted"
