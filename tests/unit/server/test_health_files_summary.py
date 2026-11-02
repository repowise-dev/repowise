"""``fields=summary`` on /api/repos/{id}/health/files.

The code-health map fires one unconditional 2,000-row request on page load and
reads none of the per-row lead or detail keys — measured on the repowise index,
1,060,407 B of which 432,081 B is keys nothing on that page touches, and the
lead is reduced from *every* open finding in the repo (10,740 rows, 1.66 MB of
``details_json``) to produce it.

``summary`` drops exactly the keys already declared optional on
``HealthFileMetric``, so a summary row still parses as one and every consumer
already handles them absent.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata, PerformanceOpportunity
from tests.unit.server.conftest import create_test_repo

_FULL_ONLY = {
    "duplication_pct",
    "defect_score",
    "primary_biomarker",
    "primary_reason",
    "total_deduction",
}


def _metric(i: int) -> dict:
    return {
        "file_path": f"src/f{i:02d}.py",
        "score": 3.0 + i,
        "max_ccn": 10 + i,
        "max_nesting": 3,
        "nloc": 100 - i,
        "has_test_file": False,
        "module": "src",
        "duplication_pct": 12.5,
        "defect_score": 3.0 + i,
        "maintainability_score": 6.0,
        "performance_score": 9.0,
    }


def _finding(path: str, biomarker: str, dimension: str, impact: float) -> dict:
    return {
        "file_path": path,
        "biomarker_type": biomarker,
        "severity": "medium",
        "function_name": "f",
        "line_start": 1,
        "line_end": 2,
        "details": {"boundary_kind": "db"},
        "health_impact": impact,
        "reason": f"{biomarker} on {path}",
        "dimension": dimension,
    }


def _perf_finding(path: str, line: int) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
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


async def _seed(app, repo_id: str) -> None:
    perf = [_perf_finding("src/f00.py", 1), _perf_finding("src/f01.py", 1)]
    link_performance_findings(perf)
    async with get_session(app.state.session_factory) as session:
        await crud.save_health_metrics(session, repo_id, [_metric(i) for i in range(3)])
        await crud.save_health_findings(
            session,
            repo_id,
            [_finding("src/f00.py", "complex_method", "defect", 2.5), *perf],
        )
        await crud.finalize_performance_opportunities(session, repo_id, analyzed_commit="a" * 40)
        # The verdict the finalizer stores for a production cause with a proven cost.
        await session.execute(
            update(PerformanceOpportunity)
            .where(PerformanceOpportunity.repository_id == repo_id)
            .values(queue_eligible=True)
        )


@pytest.mark.asyncio
async def test_full_is_the_default_and_carries_every_key(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    default = (await client.get(f"/api/repos/{repo['id']}/health/files")).json()
    explicit = (
        await client.get(f"/api/repos/{repo['id']}/health/files", params={"fields": "full"})
    ).json()

    assert default == explicit, "the default must stay the wide row"
    row = next(f for f in default["files"] if f["file_path"] == "src/f00.py")
    assert set(row) >= _FULL_ONLY
    assert row["primary_biomarker"] == "complex_method"
    assert row["total_deduction"] == 2.5


@pytest.mark.asyncio
async def test_summary_omits_the_optional_keys_rather_than_nulling_them(
    client: AsyncClient, app
) -> None:
    """Omitted, not nulled — most of the saving is the key names themselves."""
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    body = (
        await client.get(f"/api/repos/{repo['id']}/health/files", params={"fields": "summary"})
    ).json()
    for row in body["files"]:
        assert not (_FULL_ONLY & set(row)), f"summary row still carries {_FULL_ONLY & set(row)}"


@pytest.mark.asyncio
async def test_summary_keeps_every_field_the_map_actually_colours_by(
    client: AsyncClient, app
) -> None:
    """The saving is only legitimate if the map can still draw itself.

    ``performance_opportunities`` is the one that makes this non-obvious: the
    narrowed read has to keep counting queued causes per file, and a summary
    mode that skipped that read would grey out the performance lens while
    every other assertion here still passed.
    """
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    full = (await client.get(f"/api/repos/{repo['id']}/health/files")).json()
    lean = (
        await client.get(f"/api/repos/{repo['id']}/health/files", params={"fields": "summary"})
    ).json()

    map_fields = {
        "file_path",
        "score",
        "nloc",
        "module",
        "line_coverage_pct",
        "has_test_file",
        "maintainability_score",
        "performance_score",
        "performance_opportunities",
        "performance_analyzed",
    }
    by_path_full = {f["file_path"]: f for f in full["files"]}
    for row in lean["files"]:
        assert map_fields <= set(row)
        wide = by_path_full[row["file_path"]]
        for key in map_fields:
            assert row[key] == wide[key], f"{key} diverged on {row['file_path']}"
    # And the counts are real, not a constant zero.
    assert by_path_full["src/f01.py"]["performance_opportunities"] == 1
    assert by_path_full["src/f00.py"]["performance_opportunities"] == 1
    assert by_path_full["src/f02.py"]["performance_opportunities"] == 0


@pytest.mark.asyncio
async def test_summary_still_windows_and_filters_identically(client: AsyncClient, app) -> None:
    """The projection subtracts keys; it must not change which rows come back."""
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    params = {"limit": 2, "offset": 1, "sort": "nloc", "order": "desc"}
    full = (await client.get(f"/api/repos/{repo['id']}/health/files", params=params)).json()
    lean = (
        await client.get(
            f"/api/repos/{repo['id']}/health/files", params={**params, "fields": "summary"}
        )
    ).json()

    assert full["total"] == lean["total"] == 3
    assert [f["file_path"] for f in full["files"]] == [f["file_path"] for f in lean["files"]]


@pytest.mark.asyncio
async def test_an_unknown_fields_value_is_rejected(client: AsyncClient) -> None:
    """A typo must not silently fall through to the wide row and be paid for."""
    repo = await create_test_repo(client)
    resp = await client.get(
        f"/api/repos/{repo['id']}/health/files", params={"fields": "lean"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_row_marks_a_file_whose_history_is_too_short(client: AsyncClient, app) -> None:
    """A new file's high score is an absent history, not a clean one."""
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    async with get_session(app.state.session_factory) as session:
        for i, commits in enumerate([2, 50]):
            session.add(
                GitMetadata(
                    id=f"gm{i}",
                    repository_id=repo["id"],
                    file_path=f"src/f{i:02d}.py",
                    commit_count_total=commits,
                )
            )

    full = (await client.get(f"/api/repos/{repo['id']}/health/files")).json()
    by_path = {f["file_path"]: f for f in full["files"]}
    assert by_path["src/f00.py"]["history"] == "short"
    assert "history" not in by_path["src/f01.py"]
    assert "history" not in by_path["src/f02.py"]

    lean = (
        await client.get(f"/api/repos/{repo['id']}/health/files", params={"fields": "summary"})
    ).json()
    assert all("history" not in f for f in lean["files"])
