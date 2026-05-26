"""Unit tests for ``replace_governance_findings`` — idempotency + isolation.

Uses an in-memory SQLite database via the shared ``async_session`` fixture.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence.crud import (
    replace_governance_findings,
    save_health_findings,
)
from repowise.core.persistence.models import HealthFinding
from tests.unit.persistence.helpers import insert_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOVERNANCE_TYPES = {"ungoverned_hotspot", "stale_governance", "contradictory_decision"}


def _gov_finding(path: str, btype: str = "ungoverned_hotspot") -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=btype,
        severity=Severity.MEDIUM,
        file_path=path,
        function_name=None,
        line_start=None,
        line_end=None,
        details={"is_hotspot": True},
        health_impact=0.7,
        reason="test",
    )


def _structural_finding(path: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="nested_complexity",
        severity=Severity.HIGH,
        file_path=path,
        function_name="do_work",
        line_start=10,
        line_end=50,
        details={},
        health_impact=1.2,
        reason="deep nesting",
    )


# ---------------------------------------------------------------------------
# Basic write
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_inserts_rows(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-test")
    findings = [
        _gov_finding("src/a.py", "ungoverned_hotspot"),
        _gov_finding("src/b.py", "stale_governance"),
    ]
    await replace_governance_findings(async_session, repo.id, findings)
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(HealthFinding.repository_id == repo.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    btypes = {r.biomarker_type for r in rows}
    assert btypes == {"ungoverned_hotspot", "stale_governance"}


# ---------------------------------------------------------------------------
# Idempotency: running twice yields same row count
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_idempotent(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-idem")
    findings = [_gov_finding("src/x.py")]

    # First write
    await replace_governance_findings(async_session, repo.id, findings)
    await async_session.commit()

    # Second write with same data
    await replace_governance_findings(async_session, repo.id, findings)
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repo.id,
            HealthFinding.biomarker_type == "ungoverned_hotspot",
        )
    )
    rows = result.scalars().all()
    # Exactly one row — no duplicates
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Isolation: structural findings are untouched
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_does_not_delete_structural(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-iso")

    # Persist a structural finding first
    await save_health_findings(async_session, repo.id, [_structural_finding("src/core.py")])
    await async_session.commit()

    # Now write governance findings
    await replace_governance_findings(async_session, repo.id, [_gov_finding("src/hot.py")])
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(HealthFinding.repository_id == repo.id)
    )
    all_rows = result.scalars().all()
    btypes = {r.biomarker_type for r in all_rows}
    # Both structural and governance findings present
    assert "nested_complexity" in btypes
    assert "ungoverned_hotspot" in btypes


# ---------------------------------------------------------------------------
# Re-run replaces old governance findings with new ones
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_replaces_on_rerun(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-replace")

    # First run: 3 findings
    first_batch = [
        _gov_finding("src/a.py"),
        _gov_finding("src/b.py"),
        _gov_finding("src/c.py"),
    ]
    await replace_governance_findings(async_session, repo.id, first_batch)
    await async_session.commit()

    # Second run: only 1 finding
    second_batch = [_gov_finding("src/only.py")]
    await replace_governance_findings(async_session, repo.id, second_batch)
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repo.id,
            HealthFinding.biomarker_type.in_(list(_GOVERNANCE_TYPES)),
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].file_path == "src/only.py"


# ---------------------------------------------------------------------------
# Empty findings list clears existing governance rows
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_empty_clears(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-clear")

    await replace_governance_findings(async_session, repo.id, [_gov_finding("src/x.py")])
    await async_session.commit()

    # Pass empty list — should delete the row
    await replace_governance_findings(async_session, repo.id, [])
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repo.id,
            HealthFinding.biomarker_type.in_(list(_GOVERNANCE_TYPES)),
        )
    )
    rows = result.scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# All three governance biomarker types are accepted
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_all_three_types(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-types")
    findings = [
        _gov_finding("a.py", "ungoverned_hotspot"),
        _gov_finding("b.py", "stale_governance"),
        _gov_finding("c.py", "contradictory_decision"),
    ]
    await replace_governance_findings(async_session, repo.id, findings)
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(HealthFinding.repository_id == repo.id)
    )
    btypes = {r.biomarker_type for r in result.scalars().all()}
    assert btypes == _GOVERNANCE_TYPES


# ---------------------------------------------------------------------------
# details_json is serialized correctly
# ---------------------------------------------------------------------------


async def test_replace_governance_findings_details_json_roundtrip(async_session):
    repo = await insert_repo(async_session, local_path="/tmp/gov-json")
    f = HealthFindingData(
        biomarker_type="stale_governance",
        severity=Severity.HIGH,
        file_path="svc/orders.py",
        function_name=None,
        line_start=None,
        line_end=None,
        details={"decision_id": "d1", "staleness_score": 0.8},
        health_impact=1.2,
        reason="stale",
    )
    await replace_governance_findings(async_session, repo.id, [f])
    await async_session.commit()

    result = await async_session.execute(
        select(HealthFinding).where(HealthFinding.repository_id == repo.id)
    )
    row = result.scalars().one()
    parsed = json.loads(row.details_json)
    assert parsed["decision_id"] == "d1"
    assert parsed["staleness_score"] == pytest.approx(0.8)
