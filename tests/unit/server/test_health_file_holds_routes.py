"""File holds on the REST health surfaces: the lists leave held findings out
and say why, while every score and KPI reads exactly what it read before.

Seeds one vendored file, one test file carrying a history marker and one
production file, all Python, so no language gate is involved.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.analysis.finding_registry import FILE_HOLDS
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis import shown as shown_module
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo

_VENDORED = "third_party/lib/minified.py"
_TEST = "tests/test_app.py"
_PROD = "src/app.py"


def _metric(path: str, score: float, origin: str, is_test: bool) -> dict:
    return {
        "file_path": path,
        "score": score,
        "defect_score": score,
        "max_ccn": 20,
        "max_nesting": 4,
        "nloc": 200,
        "has_test_file": False,
        "module": path.split("/")[0],
        "code_origin": origin,
        "is_test": is_test,
    }


def _finding(path: str, kind: str, impact: float) -> dict:
    return {
        "file_path": path,
        "biomarker_type": kind,
        "severity": "high",
        "function_name": "run",
        "line_start": 1,
        "line_end": 80,
        "details": {},
        "health_impact": impact,
        "reason": f"{kind} on {path}",
    }


async def _seed(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                _metric(_VENDORED, 2.0, "vendored", False),
                _metric(_TEST, 7.0, "test", True),
                _metric(_PROD, 5.0, "production", False),
            ],
        )
        await crud.save_health_findings(
            session,
            repo_id,
            [
                _finding(_VENDORED, "large_method", 4.0),
                _finding(_VENDORED, "complex_method", 3.0),
                _finding(_TEST, "ownership_risk", 1.0),
                _finding(_PROD, "complex_method", 2.0),
            ],
        )


def _no_holds(monkeypatch) -> None:
    monkeypatch.setattr(shown_module, "_file_holds", lambda *a, **k: [])
    from repowise.core.analysis import finding_registry

    monkeypatch.setattr(finding_registry, "row_hold", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_file_page_lists_no_held_finding_and_says_why(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    page = (await client.get(f"/api/repos/{repo['id']}/files/{_VENDORED}")).json()
    assert page["health"]["findings"] == []
    assert page["health"]["gated"] == {"vendored": {"count": 2, "reason": FILE_HOLDS["vendored"]}}
    assert page["health"]["metric"]["score"] == 2.0


@pytest.mark.asyncio
async def test_findings_list_header_and_files_list_say_what_is_held(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    resp = await client.get(f"/api/repos/{repo['id']}/health/findings")
    assert {f["file_path"] for f in resp.json()} == {_PROD}
    gated = json.loads(resp.headers["X-Repowise-Gated"])
    assert gated["vendored"]["count"] == 2 and gated["test_history"]["count"] == 1

    files = (await client.get(f"/api/repos/{repo['id']}/health/files")).json()
    rows = {r["file_path"]: r for r in files["files"]}
    assert rows[_VENDORED]["held_reason"] == FILE_HOLDS["vendored"]
    assert "held_reason" not in rows[_PROD]
    assert files["gated"]["vendored"]["count"] == 2


@pytest.mark.asyncio
async def test_scores_and_kpis_do_not_change_with_the_holds(
    client: AsyncClient, app, monkeypatch
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    def kpis(overview: dict, summary: dict) -> tuple:
        s = overview["summary"]
        h = summary["health"]
        return (
            s.get("average_health"),
            s.get("worst_performer_path"),
            s.get("open_findings"),
            s.get("severity_breakdown"),
            h.get("average_health"),
            h.get("worst_performer_path"),
            h.get("open_findings"),
            h.get("severity_breakdown"),
        )

    held = kpis(
        (await client.get(f"/api/repos/{repo['id']}/health/overview")).json(),
        (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json(),
    )
    async with get_session(app.state.session_factory) as session:
        held_summary = await crud.get_health_summary(session, repo["id"])
    _no_holds(monkeypatch)
    plain = kpis(
        (await client.get(f"/api/repos/{repo['id']}/health/overview")).json(),
        (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json(),
    )
    async with get_session(app.state.session_factory) as session:
        plain_summary = await crud.get_health_summary(session, repo["id"])
    assert held == plain
    assert held_summary == plain_summary
