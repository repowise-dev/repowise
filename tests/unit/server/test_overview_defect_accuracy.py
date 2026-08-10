"""The ``defect_accuracy`` block of /api/repos/{id}/overview-summary.

The stat reads its per-file fix count and its window out of each finding's
``details``. This route built its finding dicts by hand and left ``details``
out, so it silently fell back to the defaults for both — reporting one recent
fix per flagged file and a 180-day window regardless of what was indexed, while
the health dashboard's copy of the same stat reported the real numbers.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo

# compute_defect_accuracy stays silent below 25 scored files / 5 defect files.
_FILES = 30
_DEFECT_FILES = 6
_WINDOW_DAYS = 90


def _metric(i: int) -> dict:
    return {
        "file_path": f"src/f{i:02d}.py",
        "score": float(i) / 3.0,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": 10,
        "has_test_file": False,
        "module": "src",
    }


def _prior_defect(i: int, count: int) -> dict:
    return {
        "file_path": f"src/f{i:02d}.py",
        "biomarker_type": "prior_defect",
        "severity": "medium",
        "function_name": None,
        "line_start": None,
        "line_end": None,
        "details": {"prior_defect_count": count, "window_days": _WINDOW_DAYS},
        "health_impact": 0.3,
        "reason": "recently bug-fixed",
    }


@pytest.mark.asyncio
async def test_defect_accuracy_reports_the_indexed_fix_counts_and_window(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        await crud.save_health_metrics(session, repo["id"], [_metric(i) for i in range(_FILES)])
        await crud.save_health_findings(
            session,
            repo["id"],
            # Deliberately not 1: the old hand-built dicts dropped ``details``,
            # so every one of these came back as a single fix.
            [_prior_defect(i, count=i + 2) for i in range(_DEFECT_FILES)]
            # Noise the stat must ignore — it reads no other biomarker.
            + [
                {
                    "file_path": "src/f29.py",
                    "biomarker_type": "complex_method",
                    "severity": "high",
                    "function_name": "big",
                    "line_start": 1,
                    "line_end": 40,
                    "details": {"prior_defect_count": 99, "window_days": 7},
                    "health_impact": 2.0,
                    "reason": "complex",
                }
            ],
        )

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    stat = resp.json()["health"]["defect_accuracy"]
    assert stat is not None

    assert stat["window_days"] == _WINDOW_DAYS
    assert stat["defect_files"] == _DEFECT_FILES
    flagged = {row["file_path"]: row["recent_fixes"] for row in stat["flagged_files"]}
    for i in range(_DEFECT_FILES):
        assert flagged[f"src/f{i:02d}.py"] == i + 2
    # The complex_method row carries a details blob that looks like a label;
    # counting it would make f29 a defect file and push defect_files to 7.
    assert flagged.get("src/f29.py", 0) == 0
