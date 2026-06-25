"""Per-dimension health scores survive the persistence round trip.

Locks BOTH metric writers - the full-reindex ``save_health_metrics`` and the
incremental ``upsert_health_metrics`` - plus the findings ``dimension`` column,
so a future edit to one writer that forgets the new fields is caught.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.models import (
    HealthFileMetricData,
    HealthFindingData,
    Severity,
)
from repowise.core.persistence.crud.analysis import (
    get_health_findings,
    get_health_metrics,
    save_health_findings,
    save_health_metrics,
    upsert_health_metrics,
)
from tests.unit.persistence.helpers import insert_repo


def _metric(path: str, **overrides) -> HealthFileMetricData:
    base = dict(
        file_path=path,
        score=7.5,
        max_ccn=3,
        max_nesting=2,
        nloc=120,
        has_test_file=False,
        defect_score=7.5,
        maintainability_score=6.0,
        performance_score=None,
    )
    base.update(overrides)
    return HealthFileMetricData(**base)


@pytest.mark.asyncio
async def test_save_health_metrics_persists_dimension_scores(async_session):
    repo = await insert_repo(async_session)
    await save_health_metrics(async_session, repo.id, [_metric("a.py")])
    await async_session.commit()

    rows = await get_health_metrics(async_session, repo.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.score == 7.5
    assert row.defect_score == 7.5
    assert row.maintainability_score == 6.0
    assert row.performance_score is None


@pytest.mark.asyncio
async def test_upsert_health_metrics_persists_dimension_scores(async_session):
    """The incremental update path must carry the new fields too."""
    repo = await insert_repo(async_session)
    # First write via upsert (insert branch).
    await upsert_health_metrics(async_session, repo.id, [_metric("b.py")])
    await async_session.commit()

    row = (await get_health_metrics(async_session, repo.id))[0]
    assert row.defect_score == 7.5
    assert row.maintainability_score == 6.0
    assert row.performance_score is None

    # Re-upsert the same path with new values (update branch).
    await upsert_health_metrics(
        async_session,
        repo.id,
        [_metric("b.py", score=4.0, defect_score=4.0, maintainability_score=2.5)],
    )
    await async_session.commit()

    rows = await get_health_metrics(async_session, repo.id)
    assert len(rows) == 1  # updated in place, not duplicated
    row = rows[0]
    assert row.score == 4.0
    assert row.defect_score == 4.0
    assert row.maintainability_score == 2.5
    assert row.performance_score is None


@pytest.mark.asyncio
async def test_save_health_findings_persists_dimension(async_session):
    repo = await insert_repo(async_session)
    findings = [
        HealthFindingData(
            biomarker_type="low_cohesion",
            severity=Severity.HIGH,
            file_path="a.py",
            function_name=None,
            line_start=1,
            line_end=10,
            details={},
            health_impact=1.2,
            dimension="maintainability",
        ),
        HealthFindingData(
            biomarker_type="change_entropy",
            severity=Severity.HIGH,
            file_path="a.py",
            function_name=None,
            line_start=1,
            line_end=10,
            details={},
            health_impact=1.5,
            dimension="defect",
        ),
    ]
    await save_health_findings(async_session, repo.id, findings)
    await async_session.commit()

    rows = await get_health_findings(async_session, repo.id)
    by_type = {r.biomarker_type: r for r in rows}
    assert by_type["low_cohesion"].dimension == "maintainability"
    assert by_type["change_entropy"].dimension == "defect"


@pytest.mark.asyncio
async def test_get_health_findings_accepts_comma_separated_biomarker_types(async_session):
    """One request can pull several biomarker types (the panels batch this way);
    a single value with no comma still matches exactly."""
    repo = await insert_repo(async_session)
    findings = [
        HealthFindingData(
            biomarker_type=bt,
            severity=Severity.HIGH,
            file_path="a.py",
            function_name=None,
            line_start=1,
            line_end=10,
            details={},
            health_impact=1.0,
            dimension="defect",
        )
        for bt in ("function_hotspot", "code_age_volatility", "hidden_coupling", "change_entropy")
    ]
    await save_health_findings(async_session, repo.id, findings)
    await async_session.commit()

    multi = await get_health_findings(
        async_session,
        repo.id,
        biomarker_type="function_hotspot,code_age_volatility,hidden_coupling",
    )
    assert {r.biomarker_type for r in multi} == {
        "function_hotspot",
        "code_age_volatility",
        "hidden_coupling",
    }

    # Single value still behaves as an exact match.
    single = await get_health_findings(
        async_session, repo.id, biomarker_type="change_entropy"
    )
    assert {r.biomarker_type for r in single} == {"change_entropy"}
