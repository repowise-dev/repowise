"""The zero-impact dimensions stay out of the ranked queues, and stay reachable.

Every performance and advisory finding carries a health impact of zero by
construction, so a list ordered by impact sorts them below every defect row.
That is not a ranking of that work, it is a tail nobody reads, and it renders
as a deduction that rounded away. The dimension answers on its own surfaces.

The defence is about ranking everything against everything. A caller naming one
file or one marker has left that ranking, so it gets every finding on the thing
it named -- which is what a per-file editor signal and a marker-filtered list
both need, and without which a marker menu built from the unfiltered breakdown
offers options that match nothing.
"""

from __future__ import annotations

from httpx import AsyncClient

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence import crud
from tests.unit.server.conftest import create_test_repo

_BIOMARKER = {
    "performance": "io_in_loop",
    "advisory": "assertion_free_test",
}


def _finding(path: str, dimension: str, impact: float) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=_BIOMARKER.get(dimension, "complex_method"),
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
            session, repo["id"], [_metric(f"src/{n}.py") for n in "abcd"]
        )
        await crud.save_health_findings(
            session,
            repo["id"],
            [
                _finding("src/a.py", "defect", 2.5),
                _finding("src/b.py", "maintainability", 1.0),
                _finding("src/c.py", "performance", 0.0),
                _finding("src/d.py", "advisory", 0.0),
            ],
        )
        await session.commit()
    return repo["id"]


async def test_the_unfiltered_findings_list_leaves_the_zero_impact_ones_out(app, client):
    repo_id = await _seed(app, client)
    rows = (await client.get(f"/api/repos/{repo_id}/health/findings")).json()
    assert {r["dimension"] for r in rows} == {"defect", "maintainability"}


async def test_a_caller_that_asks_gets_every_finding_on_a_file(app, client):
    """The per-file fetch behind every editor signal. It ranks nothing.

    A flag rather than an inference from ``file_path``, because the web
    expander names a path too and wants the opposite: exactly the set the row
    above it counted.
    """
    repo_id = await _seed(app, client)
    for path, dimension in (("src/c.py", "performance"), ("src/d.py", "advisory")):
        params = {"file_path": path, "include_zero_impact": "true"}
        rows = (
            await client.get(f"/api/repos/{repo_id}/health/findings", params=params)
        ).json()
        assert [r["dimension"] for r in rows] == [dimension], path


async def test_naming_a_file_alone_stays_in_the_ranked_set(app, client):
    repo_id = await _seed(app, client)
    for path in ("src/c.py", "src/d.py"):
        rows = (
            await client.get(
                f"/api/repos/{repo_id}/health/findings", params={"file_path": path}
            )
        ).json()
        assert rows == [], path


async def test_naming_a_marker_returns_its_findings(app, client):
    repo_id = await _seed(app, client)
    rows = (
        await client.get(
            f"/api/repos/{repo_id}/health/findings",
            params={"biomarker_type": "assertion_free_test"},
        )
    ).json()
    assert [r["file_path"] for r in rows] == ["src/d.py"]


async def test_asking_does_not_widen_the_list_past_the_file_named(app, client):
    """The escape is from the dimension filter, not from the file filter."""
    repo_id = await _seed(app, client)
    rows = (
        await client.get(
            f"/api/repos/{repo_id}/health/findings",
            params={"file_path": "src/a.py", "include_zero_impact": "true"},
        )
    ).json()
    assert [r["file_path"] for r in rows] == ["src/a.py"]


async def test_the_work_queue_ranks_without_the_zero_impact_dimensions(app, client):
    repo_id = await _seed(app, client)
    queue = (await client.get(f"/api/repos/{repo_id}/health/refactoring-targets")).json()
    assert {t["file_path"] for t in queue["targets"]} == {"src/a.py", "src/b.py"}


async def test_a_file_with_only_advisory_findings_has_no_lead(app, client):
    """And the overview still answers.

    ``primary_finding`` returns None for a file no finding accuses, which is
    deliberate: an advisory marker deducts nothing, so naming one as the reason
    a file is unhealthy would print a cause beside a deduction of zero. Every
    reader of it has to treat that None as an answer.
    """
    repo_id = await _seed(app, client)
    response = await client.get(f"/api/repos/{repo_id}/health/overview")
    assert response.status_code == 200
    lead = next(f for f in response.json()["files"] if f["file_path"] == "src/d.py")
    assert lead["primary_biomarker"] is None
    assert lead["primary_reason"] is None
    assert lead["total_deduction"] == 0.0


async def test_a_queue_row_filtered_to_an_advisory_marker_leads_with_it(app, client):
    """The one reader that cannot pass the refusal on: the row needs a lead."""
    repo_id = await _seed(app, client)
    queue = (
        await client.get(
            f"/api/repos/{repo_id}/health/refactoring-targets",
            params={"biomarker": "assertion_free_test"},
        )
    ).json()
    assert [t["file_path"] for t in queue["targets"]] == ["src/d.py"]
    assert queue["targets"][0]["primary_biomarker"] == "assertion_free_test"
    assert queue["targets"][0]["total_impact"] == 0.0


async def test_every_marker_the_menu_offers_returns_a_row(app, client):
    """The marker menu is built from the unfiltered breakdown, so it must.

    Selecting an option that can never match reads as "no such work", which is
    the opposite of what the breakdown that offered it said.
    """
    repo_id = await _seed(app, client)
    overview = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()
    offered = sorted({b["biomarker_type"] for b in overview["biomarkers"]})
    assert "assertion_free_test" in offered, offered
    for marker in offered:
        queue = (
            await client.get(
                f"/api/repos/{repo_id}/health/refactoring-targets",
                params={"biomarker": marker},
            )
        ).json()
        assert queue["targets"], marker
        assert {t["primary_biomarker"] for t in queue["targets"]} == {marker}


async def test_asking_for_the_dimension_still_returns_it(app, client):
    repo_id = await _seed(app, client)
    for dimension, path in (("performance", "src/c.py"), ("advisory", "src/d.py")):
        rows = (
            await client.get(
                f"/api/repos/{repo_id}/health/findings", params={"dimension": dimension}
            )
        ).json()
        assert [r["file_path"] for r in rows] == [path], dimension


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
    ranked = {f["dimension"] for f in overview["top_findings"]}
    assert ranked == {"defect", "maintainability"}
    # The rollup above the queue still counts performance: excluding a row from
    # a ranking is not the same as pretending it does not exist. Advisory is
    # the one dimension it also leaves out, because it is not open work.
    assert overview["summary"]["open_findings"] == 3
    # The marker breakdown behind the marker menu counts all four, which is
    # what makes the menu wider than any ranked list it filters.
    assert {b["biomarker_type"] for b in overview["biomarkers"]} == {
        "complex_method",
        "io_in_loop",
        "assertion_free_test",
    }
