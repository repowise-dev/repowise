"""REST surfaces honour the layer x language gates.

Each route hides rows on a gated language (Java here) by default, returns
them with ``include_unverified=true``, counts what it held back in ``gated``,
and leaves the Python rows beside them alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.models import DeadCodeFinding
from tests.unit.server.conftest import create_test_repo

JAVA = "src/Billing.java"
PY = "src/billing.py"


def _finding(path: str, function_name: str) -> dict[str, Any]:
    return {
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": "high",
        "function_name": function_name,
        "line_start": 10,
        "line_end": 30,
        "details": {"ccn": 16},
        "health_impact": 3.0,
        "reason": "seeded",
        "dimension": "defect",
    }


def _plan(path: str, symbol: str) -> dict[str, Any]:
    return {
        "refactoring_type": "extract_method",
        "file_path": path,
        "target_symbol": symbol,
        "line_start": 10,
        "line_end": 30,
        "plan": {"extracted_name": f"_{symbol}_part", "span": {"start": 12, "end": 28}},
        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }


def _perf(path: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=10,
        line_end=10,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [f"{path}::run", "src/db.py::fetch"],
            "resolution_basis": "reliable-edge",
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )


async def _seed(client: AsyncClient, app) -> str:
    repo_id = (await create_test_repo(client))["id"]
    perf = [_perf(JAVA), _perf(PY)]
    link_performance_findings(perf)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                {"file_path": p, "score": 4.0, "max_ccn": 16, "max_nesting": 2, "nloc": 100}
                for p in (JAVA, PY)
            ],
        )
        await crud.save_health_findings(
            session, repo_id, [_finding(JAVA, "charge"), _finding(PY, "charge"), *perf]
        )
        await crud.save_refactoring_suggestions(
            session, repo_id, [_plan(JAVA, "charge"), _plan(PY, "charge")]
        )
        await crud.finalize_refactoring_opportunities(session, repo_id, analyzed_commit="c" * 40)
        await crud.finalize_performance_opportunities(session, repo_id, analyzed_commit="c" * 40)
        now = datetime.now(UTC)
        for i, path in enumerate((JAVA, PY)):
            session.add(
                DeadCodeFinding(
                    id=f"dead{i}",
                    repository_id=repo_id,
                    kind="unused_export",
                    file_path=path,
                    symbol_name="old",
                    symbol_kind="function",
                    confidence=0.9,
                    reason="no callers",
                    lines=10,
                    safe_to_delete=True,
                    status="open",
                    analyzed_at=now,
                )
            )
        await session.commit()
    return repo_id


async def _get(client: AsyncClient, url: str, **params: Any) -> Any:
    resp = await client.get(url, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _paths(rows: list[dict]) -> set[str]:
    return {row["file_path"] for row in rows}


async def test_health_findings_and_overview(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/health"

    assert _paths(await _get(client, f"{base}/findings")) == {PY}
    assert JAVA in _paths(await _get(client, f"{base}/findings", include_unverified=True))

    overview = await _get(client, f"{base}/overview")
    assert JAVA not in _paths(overview["top_findings"])
    assert overview["summary"]["gated"]["java"]["count"] == 2
    # The score still counts the gated finding, so the file still ranks.
    assert JAVA in _paths(overview["files"])
    opted = await _get(client, f"{base}/overview", include_unverified=True)
    assert JAVA in _paths(opted["top_findings"]) and opted["summary"]["gated"] == {}

    drawer = await _get(client, f"{base}/files/breakdown", file_path=JAVA)
    assert drawer["findings"] == [] and drawer["gated"]["java"]["count"] == 2


async def test_dead_code_list_and_summary(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/dead-code"

    assert _paths(await _get(client, base)) == {PY}
    assert _paths(await _get(client, base, include_unverified=True)) == {PY, JAVA}
    summary = await _get(client, f"{base}/summary")
    assert summary["total_findings"] == 1 and summary["gated"]["java"]["count"] == 1
    assert (await _get(client, f"{base}/summary", include_unverified=True))["gated"] == {}


async def test_refactoring_plans_opportunities_and_rollup(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/refactoring"

    # The extract plan, and the performance plan the perf finalizer wrote.
    every = (await _get(client, f"{base}/targets", include_unverified=True))["plans"]
    java_plans = sum(1 for plan in every if plan["file_path"] == JAVA)
    assert java_plans >= 1
    targets = await _get(client, f"{base}/targets")
    assert _paths(targets["plans"]) == {PY}
    assert targets["gated"]["java"]["count"] == java_plans
    page = await _get(client, f"{base}/targets/page")
    assert _paths(page["items"]) == {PY} and page["gated"]["java"]["count"] == java_plans

    queue = await _get(client, f"{base}/opportunities", scope="all")
    assert _paths(queue["items"]) == {PY} and queue["gated"]["java"]["count"] == 1
    opted = await _get(client, f"{base}/opportunities", scope="all", include_unverified=True)
    assert _paths(opted["items"]) == {PY, JAVA} and opted["gated"] == {}

    rollup = await _get(client, f"{base}/summary")
    assert rollup["summary"]["gated"]["java"]["count"] == 1
    lead = rollup["summary"]["lead"]
    assert lead is None or lead["file_path"] == PY


async def test_performance_queue(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    url = f"/api/repos/{repo_id}/health/performance-opportunities"

    page = await _get(client, url, context="all")
    assert JAVA not in {i.get("file_path") for i in page["items"]}
    assert page["gated"]["java"]["count"] == 1
    # The headline is recounted without the gated cause.
    assert page["summary"]["total"] == page["total"]
    opted = await _get(client, url, context="all", include_unverified=True)
    assert opted["total"] == page["total"] + 1 and opted["gated"] == {}
