"""Performance stays out of the impact-ranked queues, and reachable on request.

Every performance finding carries a health impact of zero by construction, so a
list ordered by impact sorts them below every defect row. That is not a ranking
of performance work, it is a tail nobody reads, and it renders as a deduction
that rounded away. The dimension answers on its own surfaces instead.
"""

from __future__ import annotations

from httpx import AsyncClient

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence import crud
from tests.unit.server.conftest import create_test_repo


def _finding(path: str, dimension: str, impact: float) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop" if dimension == "performance" else "complex_method",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=1,
        line_end=2,
        details={},
        health_impact=impact,
        reason="reason",
        dimension=dimension,
    )


def _metric(path: str) -> dict:
    return {
        "file_path": path,
        "score": 7.0,
        "max_ccn": 3,
        "max_nesting": 2,
        "nloc": 40,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": "src",
    }


async def _seed(app, client: AsyncClient) -> str:
    repo = await create_test_repo(client)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(
            session, repo["id"], [_metric(f"src/{n}.py") for n in "abc"]
        )
        await crud.save_health_findings(
            session,
            repo["id"],
            [
                _finding("src/a.py", "defect", 2.5),
                _finding("src/b.py", "maintainability", 1.0),
                _finding("src/c.py", "performance", 0.0),
            ],
        )
        await session.commit()
    return repo["id"]


async def test_the_unfiltered_findings_list_leaves_performance_out(app, client):
    repo_id = await _seed(app, client)
    rows = (await client.get(f"/api/repos/{repo_id}/health/findings")).json()
    assert {r["dimension"] for r in rows} == {"defect", "maintainability"}


async def test_asking_for_the_dimension_still_returns_it(app, client):
    repo_id = await _seed(app, client)
    rows = (
        await client.get(
            f"/api/repos/{repo_id}/health/findings", params={"dimension": "performance"}
        )
    ).json()
    assert [r["file_path"] for r in rows] == ["src/c.py"]


async def test_a_null_dimension_still_reads_as_defect_work(app, client):
    """Rows written before the split carry NULL, and NULL homes under defect."""
    repo = await create_test_repo(client)
    async with app.state.session_factory() as session:
        finding = _finding("src/legacy.py", "defect", 1.5)
        finding.dimension = None
        await crud.save_health_findings(session, repo["id"], [finding])
        await session.commit()
    rows = (await client.get(f"/api/repos/{repo['id']}/health/findings")).json()
    assert [r["file_path"] for r in rows] == ["src/legacy.py"]


async def test_the_overview_queue_leaves_performance_out_but_still_counts_it(app, client):
    repo_id = await _seed(app, client)
    overview = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()
    assert all(f["dimension"] != "performance" for f in overview["top_findings"])
    # The rollups above the queue still see every dimension: excluding a row
    # from a ranking is not the same as pretending it does not exist.
    assert overview["summary"]["open_findings"] == 3
