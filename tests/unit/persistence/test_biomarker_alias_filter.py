"""A findings filter written with a renamed biomarker's old id still matches."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence.crud.analysis import get_health_findings, save_health_findings
from tests.unit.persistence.helpers import insert_repo

OLD = "bumpy_road"
NEW = "repeated_nesting"


@pytest.mark.asyncio
async def test_a_findings_filter_on_the_old_id_returns_the_renamed_rows(async_session):
    repo = await insert_repo(async_session)
    finding = HealthFindingData(
        biomarker_type=NEW,
        severity=Severity.MEDIUM,
        file_path="src/a.py",
        function_name="walk",
        line_start=3,
        line_end=40,
        details={"bumps": 4},
        health_impact=0.5,
    )
    await save_health_findings(async_session, repo.id, [finding])
    await async_session.commit()

    rows = await get_health_findings(async_session, repo.id, biomarker_type=f"{OLD},large_method")
    assert [r.biomarker_type for r in rows] == [NEW]
