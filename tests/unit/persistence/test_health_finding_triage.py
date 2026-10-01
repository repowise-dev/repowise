"""Triage on a health finding survives the next index, on every writer.

A re-detected finding folds into the triaged row that holds its public id:
``acknowledged`` and ``false_positive`` stand, ``resolved`` reopens, and an
open row is simply replaced. No public id ever names two rows.
"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.finding_identity import legacy_finding_public_id
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence.crud.analysis import (
    replace_governance_findings,
    save_health_findings,
    update_health_finding_status,
    upsert_health_findings,
)
from repowise.core.persistence.models import HealthFinding
from tests.unit.persistence.helpers import insert_repo


def _finding(function: str, *, reason: str = "first", severity=Severity.MEDIUM, **kw):
    base = dict(
        biomarker_type="complex_method",
        severity=severity,
        file_path="a.py",
        function_name=function,
        line_start=10,
        line_end=20,
        details={"ccn": 12, "symbol_line": 10},
        health_impact=0.5,
        reason=reason,
        dimension="defect",
    )
    base.update(kw)
    return HealthFindingData(**base)


async def _rows(session, repo_id) -> list[HealthFinding]:
    result = await session.execute(
        select(HealthFinding).where(HealthFinding.repository_id == repo_id)
    )
    return list(result.scalars().all())


async def _write(session, repo_id, writer, findings):
    if writer == "save":
        await save_health_findings(session, repo_id, findings)
    else:
        await upsert_health_findings(session, repo_id, findings, file_paths=["a.py"])
    await session.commit()


async def _triage(session, repo_id, status_by_function: dict[str, str]):
    for row in await _rows(session, repo_id):
        if row.function_name in status_by_function:
            await update_health_finding_status(session, row.id, status_by_function[row.function_name])
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["save", "upsert"])
async def test_triage_carries_over_by_public_id(async_session, writer):
    repo = await insert_repo(async_session)
    names = ["acked", "wrong", "fixed", "plain"]
    await _write(async_session, repo.id, writer, [_finding(n) for n in names])
    await _triage(
        async_session,
        repo.id,
        {"acked": "acknowledged", "wrong": "false_positive", "fixed": "resolved"},
    )
    created = {r.function_name: r.created_at for r in await _rows(async_session, repo.id)}

    await _write(
        async_session,
        repo.id,
        writer,
        [
            # The same findings two lines lower (an edit above the function),
            # and one point more complex.
            _finding(
                n,
                reason="second",
                severity=Severity.HIGH,
                line_start=12,
                line_end=22,
                details={"ccn": 13, "symbol_line": 12},
            )
            for n in names
        ],
    )

    rows = await _rows(async_session, repo.id)
    assert Counter(r.public_id for r in rows).most_common(1)[0][1] == 1
    by_name = {r.function_name: r for r in rows}
    assert len(rows) == len(names)
    assert by_name["acked"].status == "acknowledged"
    assert by_name["wrong"].status == "false_positive"
    assert by_name["fixed"].status == "open"
    assert by_name["plain"].status == "open"
    for name in ("acked", "wrong", "fixed"):
        row = by_name[name]
        assert row.created_at == created[name]
        assert (row.reason, row.severity, row.line_start) == ("second", "high", 12)


@pytest.mark.asyncio
async def test_a_triaged_row_outside_the_upsert_scope_is_untouched(async_session):
    repo = await insert_repo(async_session)
    await save_health_findings(
        async_session, repo.id, [_finding("f"), _finding("f", file_path="b.py")]
    )
    await async_session.commit()
    for row in await _rows(async_session, repo.id):
        await update_health_finding_status(async_session, row.id, "acknowledged")
    await async_session.commit()

    await upsert_health_findings(async_session, repo.id, [_finding("f")], file_paths=["a.py"])
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert sorted((r.file_path, r.status) for r in rows) == [
        ("a.py", "acknowledged"),
        ("b.py", "acknowledged"),
    ]


@pytest.mark.asyncio
async def test_duplicate_triaged_rows_collapse_to_one(async_session):
    """A store the old writers left with an acked row and its acked twin."""
    repo = await insert_repo(async_session)
    await save_health_findings(async_session, repo.id, [_finding("f")])
    await async_session.commit()
    (row,) = await _rows(async_session, repo.id)
    async_session.add(
        HealthFinding(
            repository_id=repo.id,
            file_path=row.file_path,
            biomarker_type=row.biomarker_type,
            severity=row.severity,
            function_name=row.function_name,
            line_start=row.line_start,
            line_end=row.line_end,
            details_json=row.details_json,
            public_id=row.public_id,
            dimension=row.dimension,
            status="acknowledged",
        )
    )
    await update_health_finding_status(async_session, row.id, "acknowledged")
    await async_session.commit()

    await save_health_findings(async_session, repo.id, [_finding("f")])
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert [r.status for r in rows] == ["acknowledged"]


@pytest.mark.asyncio
async def test_a_row_triaged_under_the_old_kernel_still_matches(async_session):
    repo = await insert_repo(async_session)
    finding = _finding("f")
    await save_health_findings(async_session, repo.id, [finding])
    await async_session.commit()
    (row,) = await _rows(async_session, repo.id)
    current = row.public_id
    row.public_id = legacy_finding_public_id(finding)
    row.status = "false_positive"
    await async_session.commit()

    await save_health_findings(async_session, repo.id, [finding])
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert [(r.status, r.public_id) for r in rows] == [("false_positive", current)]


@pytest.mark.asyncio
async def test_governance_triage_survives_its_rewrite(async_session):
    repo = await insert_repo(async_session)
    finding = _finding(
        None,
        biomarker_type="ungoverned_hotspot",
        line_start=None,
        line_end=None,
        details={},
        health_impact=0.0,
    )
    await replace_governance_findings(async_session, repo.id, [finding])
    await async_session.commit()
    (row,) = await _rows(async_session, repo.id)
    await update_health_finding_status(async_session, row.id, "acknowledged")
    await async_session.commit()

    await replace_governance_findings(async_session, repo.id, [finding])
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert [(r.id, r.status) for r in rows] == [(row.id, "acknowledged")]
