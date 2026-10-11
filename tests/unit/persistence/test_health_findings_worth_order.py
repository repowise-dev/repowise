"""The default findings read puts work worth doing first ahead, and a filtered
read still tiers a function by every finding on it."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence.crud.analysis import (
    get_health_findings,
    health_finding_priorities,
    save_health_findings,
)
from tests.unit.persistence.helpers import insert_repo


def _finding(marker: str, function: str, impact: float, **details) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.HIGH,
        file_path="a.py",
        function_name=function,
        line_start=1,
        line_end=200,
        details=details,
        health_impact=impact,
    )


@pytest.mark.asyncio
async def test_worth_first_then_impact_and_filters_keep_the_whole_shape(async_session):
    repo = await insert_repo(async_session)
    await save_health_findings(
        async_session,
        repo.id,
        [
            # Near the bar, but the highest impact.
            _finding("complex_method", "tidy", 3.0, ccn=14, nloc=40),
            # Tangled: its CCN is on the complex_method row, its depth on this one.
            _finding("nested_complexity", "knot", 0.5, max_nesting=5),
            _finding("complex_method", "knot", 0.4, ccn=27, nloc=80),
        ],
    )
    await async_session.commit()

    rows = await get_health_findings(async_session, repo.id)
    assert [(f.function_name, f.biomarker_type) for f in rows] == [
        ("knot", "nested_complexity"),
        ("knot", "complex_method"),
        ("tidy", "complex_method"),
    ]

    # Filtered to the nesting marker alone, the function keeps its CCN.
    nested = await get_health_findings(async_session, repo.id, biomarker_type="nested_complexity")
    reasons = await health_finding_priorities(async_session, repo.id, nested)
    assert [reasons[f.id] for f in nested] == [None]
