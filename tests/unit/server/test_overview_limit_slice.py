"""``/health/overview`` builds the full file row only for the files it lists.

The distribution and the defect-accuracy stat read path, score and NLOC off
every file, so they must not change with ``limit``; the listed rows must be the
same rows, leads included, whatever the limit.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo

_FILES = 30


def _metric(i: int) -> dict:
    return {
        "file_path": f"src/f{i:02d}.py",
        # One score sits a hair under a band edge: the distribution reads the
        # score at the rounding the file row carries, so it lands Excellent.
        "score": 8.4996 if i == 29 else float(i) / 3.0,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": 10 + i,
        "has_test_file": False,
        "module": "src",
    }


def _finding(i: int, kind: str, impact: float) -> dict:
    return {
        "file_path": f"src/f{i:02d}.py",
        "biomarker_type": kind,
        "severity": "medium",
        "function_name": None,
        "line_start": None,
        "line_end": None,
        "details": {"prior_defect_count": 2, "window_days": 90},
        "health_impact": impact,
        "reason": f"{kind} on {i}",
    }


@pytest.mark.asyncio
async def test_listed_rows_and_repo_figures_do_not_move_with_the_limit(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.save_health_metrics(session, repo["id"], [_metric(i) for i in range(_FILES)])
        await crud.save_health_findings(
            session,
            repo["id"],
            [_finding(i, "prior_defect", 0.3) for i in range(6)]
            + [_finding(i, "low_cohesion", 0.4 + i / 10) for i in range(0, _FILES, 2)],
        )

    async def overview(limit: int) -> dict:
        resp = await client.get(f"/api/repos/{repo['id']}/health/overview", params={"limit": limit})
        assert resp.status_code == 200, resp.text
        return resp.json()

    narrow, wide = await overview(3), await overview(200)

    assert len(narrow["files"]) == 3
    assert len(wide["files"]) == _FILES
    assert narrow["files"] == wide["files"][:3]
    # Leads still reach the listed rows.
    assert any(row["primary_biomarker"] for row in narrow["files"])
    assert narrow["distribution"] == wide["distribution"]
    assert narrow["distribution"]["total_files"] == _FILES
    assert narrow["distribution"]["bands"]["excellent"]["files"] >= 1
    assert narrow["defect_accuracy"] == wide["defect_accuracy"]
    assert narrow["defect_accuracy"] is not None
