"""The history hold on the REST health surfaces: finding lists leave history
markers on production files out and count them under ``history``, while each
file's score breakdown, its lead and the work queue's expander still read
what the score and the queue counted.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.analysis.finding_registry import FILE_HOLDS
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo
from tests.unit.server.test_health_file_holds_routes import _finding, _metric, _no_holds

_PROD = "src/app.py"
_HISTORY_ONLY = "src/legacy.py"


async def _seed(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                _metric(_PROD, 5.5, "production", False),
                _metric(_HISTORY_ONLY, 8.0, "production", False),
            ],
        )
        await crud.save_health_findings(
            session,
            repo_id,
            [
                _finding(_PROD, "complex_method", 2.0),
                _finding(_PROD, "ownership_risk", 1.5),
                _finding(_PROD, "change_entropy", 1.0),
                _finding(_HISTORY_ONLY, "ownership_risk", 2.0),
            ],
        )


async def _get(client: AsyncClient, url: str, **params) -> dict:
    resp = await client.get(url, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_repo_list_holds_history_and_says_so(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    resp = await client.get(f"/api/repos/{repo['id']}/health/findings")
    assert {(f["file_path"], f["biomarker_type"]) for f in resp.json()} == {
        (_PROD, "complex_method")
    }
    gated = json.loads(resp.headers["X-Repowise-Gated"])
    assert gated["history"] == {"count": 3, "reason": FILE_HOLDS["history"]}


@pytest.mark.asyncio
async def test_queue_count_matches_its_expander(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/health"
    for history in ("exclude", "include"):
        queue = await _get(client, f"{base}/refactoring-targets", history=history)
        assert queue["targets"], history
        for row in queue["targets"]:
            resp = await client.get(f"{base}/findings", params={"file_path": row["file_path"]})
            assert len(resp.json()) == row["finding_count"], (history, row["file_path"])
            assert "history" not in json.loads(resp.headers.get("X-Repowise-Gated", "{}"))
    included = await _get(client, f"{base}/refactoring-targets", history="include")
    assert _HISTORY_ONLY in {r["file_path"] for r in included["targets"]}


@pytest.mark.asyncio
async def test_breakdown_and_lead_read_what_the_score_counted(
    client: AsyncClient, app, monkeypatch
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}"

    drawer = await _get(client, f"{base}/health/files/breakdown", file_path=_PROD)
    page = (await _get(client, f"{base}/files/{_PROD}"))["health"]
    files = {r["file_path"]: r for r in (await _get(client, f"{base}/health/files"))["files"]}
    # The lists hold the markers and count them.
    assert [f["biomarker_type"] for f in drawer["findings"]] == ["complex_method"]
    assert drawer["gated"]["history"]["count"] == 2
    assert [f["biomarker_type"] for f in page["findings"]] == ["complex_method"]
    assert page["gated"]["history"]["count"] == 2
    # A history-only file still has a lead while its score is below 10.
    assert files[_HISTORY_ONLY]["primary_biomarker"] == "ownership_risk"

    _no_holds(monkeypatch)
    plain_drawer = await _get(client, f"{base}/health/files/breakdown", file_path=_PROD)
    plain_page = (await _get(client, f"{base}/files/{_PROD}"))["health"]
    plain_files = {
        r["file_path"]: r for r in (await _get(client, f"{base}/health/files"))["files"]
    }
    # Breakdown and lead are the same with and without the hold.
    assert drawer["breakdown"] == plain_drawer["breakdown"]
    assert drawer["metric"] == plain_drawer["metric"]
    assert page["breakdown"] == plain_page["breakdown"]
    assert page["metric"] == plain_page["metric"]
    assert files == plain_files
