"""Only ``untested_complexity`` findings on measured coverage are served by default.

An estimate rests on call-graph reach, so it is held back like a gated row:
shown when the type is asked for by name or the caller opts into unverified rows.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis.finding_registry import (
    ROW_HOLDS,
    gated_summary,
    is_estimated,
    split_held,
)
from repowise.core.persistence.crud.analysis import get_health_findings
from repowise.core.persistence.models import HealthFinding
from tests.unit.persistence.helpers import insert_repo


def _details(basis: str, crap: float) -> dict:
    tier = {"coverage_pct": 0.0} if basis == "measured" else {"coverage_tier": "none"}
    return {"crap": crap, "ccn": 7, "coverage_basis": basis, "action": "add_tests", **tier}


def test_is_estimated_reads_the_basis() -> None:
    assert is_estimated("untested_complexity", _details("estimated", 42))
    assert is_estimated("untested_complexity", json.dumps(_details("estimated", 42)))
    assert not is_estimated("untested_complexity", _details("measured", 42))
    assert not is_estimated("complex_method", {"coverage_basis": "estimated"})
    assert not is_estimated("untested_complexity", "not json")


def test_in_memory_rows_hold_estimates_and_say_why() -> None:
    rows = [
        {"biomarker_type": "untested_complexity", "file_path": "a.py", "details": _details("measured", 56)},
        {"biomarker_type": "untested_complexity", "file_path": "a.py", "details": _details("estimated", 42)},
    ]
    kept, counts = split_held("health", rows, {}, kind="biomarker_type", get=lambda r, k: r.get(k))
    assert [r["details"]["coverage_basis"] for r in kept] == ["measured"]
    assert counts == {"estimated": 1}
    assert gated_summary("health", counts) == {"estimated": {"count": 1, "reason": ROW_HOLDS["estimated"]}}


async def _seed(session, repo_id: str) -> None:
    for basis, crap in (("measured", 56.0), ("estimated", 42.0)):
        session.add(
            HealthFinding(
                repository_id=repo_id,
                file_path="src/app.py",
                biomarker_type="untested_complexity",
                severity="high" if basis == "measured" else "medium",
                function_name=f"f_{basis}",
                line_start=1,
                line_end=20,
                details_json=json.dumps(_details(basis, crap)),
                dimension="advisory",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_the_shared_list_serves_measured_rows_by_default(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    rows = await get_health_findings(async_session, repo.id)
    assert [r.function_name for r in rows] == ["f_measured"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"biomarker_type": "untested_complexity"}, {"include_unverified": True}]
)
async def test_asking_by_name_or_opting_in_serves_estimates(async_session, kwargs) -> None:
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    rows = await get_health_findings(async_session, repo.id, **kwargs)
    assert sorted(r.function_name for r in rows) == ["f_estimated", "f_measured"]
