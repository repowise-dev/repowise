"""Re-scoring git-derived markers on files an update never walked."""

from __future__ import annotations

import json

from repowise.core.analysis.health.history_refresh import (
    REFRESHABLE_MARKERS,
    as_biomarker_result,
    refresh_history,
)
from repowise.core.analysis.health.models import (
    HealthFileMetricData,
    HealthFindingData,
    Severity,
)


def _metric(path: str = "src/auth.py", **kwargs) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=kwargs.pop("score", 8.0),
        max_ccn=9,
        max_nesting=3,
        nloc=kwargs.pop("nloc", 400),
        has_test_file=True,
        module="src",
        **kwargs,
    )


def _finding(biomarker: str, severity: Severity = Severity.HIGH, **kwargs) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=biomarker,
        severity=severity,
        file_path=kwargs.pop("file_path", "src/auth.py"),
        function_name=None,
        line_start=None,
        line_end=None,
        details=kwargs.pop("details", {}),
        health_impact=kwargs.pop("health_impact", 1.2),
    )


def _run(*, git_meta: dict, findings: list) -> list:
    return refresh_history(
        metrics=[_metric()],
        findings_by_path={"src/auth.py": findings},
        git_meta_by_path={"src/auth.py": git_meta},
        languages={"src/auth.py": "python"},
    )


def test_parse_free_markers_only() -> None:
    """The two that need a blame index and function metrics are excluded.

    Refreshing them without a parse would mean inventing their inputs.
    """
    assert "function_hotspot" not in REFRESHABLE_MARKERS
    assert "code_age_volatility" not in REFRESHABLE_MARKERS
    assert "prior_defect" in REFRESHABLE_MARKERS
    assert "co_change_scatter" in REFRESHABLE_MARKERS


def test_a_marker_that_stopped_firing_stops_deducting() -> None:
    """The point of the pass: history that has moved on stops holding a score down."""
    stale = _finding("prior_defect", details={"fix_count": 9})
    (refreshed,) = _run(git_meta={"prior_defect_count": 0}, findings=[stale])
    assert not any(f.biomarker_type == "prior_defect" for f in refreshed.findings)
    assert refreshed.history_deduction == 0.0
    assert refreshed.score > 8.0


def test_structural_findings_survive_untouched() -> None:
    """Nothing here reads the source, so nothing here may discard what a parse found."""
    structural = _finding("god_class", Severity.CRITICAL)
    (refreshed,) = _run(git_meta={"prior_defect_count": 0}, findings=[structural])
    kept = [f for f in refreshed.findings if f.biomarker_type == "god_class"]
    assert len(kept) == 1
    assert refreshed.structure_deduction > 0


def test_a_file_with_no_git_metadata_is_left_alone() -> None:
    result = refresh_history(
        metrics=[_metric()],
        findings_by_path={"src/auth.py": [_finding("prior_defect")]},
        git_meta_by_path={},
        languages={"src/auth.py": "python"},
    )
    assert result == []


def test_governance_findings_survive_without_being_scored() -> None:
    """They are produced after scoring and deduct nothing.

    Rescoring them here would invent a deduction and charge it to the capped
    organizational category, which is the one this pass is rewriting; dropping
    them would delete findings a different pass owns.
    """
    stored = _finding("ungoverned_hotspot", Severity.MEDIUM, health_impact=0.0)
    (refreshed,) = _run(git_meta={"prior_defect_count": 0}, findings=[stored])
    carried = [f for f in refreshed.findings if f.biomarker_type == "ungoverned_hotspot"]
    assert len(carried) == 1
    assert carried[0].health_impact == 0.0
    assert refreshed.history_deduction == 0.0
    assert refreshed.score == 10.0


def test_a_row_whose_split_was_never_recorded_is_backfilled() -> None:
    """The first update after the split ships must fill it in.

    Separately from the findings: rewriting those deletes and re-inserts them,
    which resets every id and ``created_at``, and a row that merely lacks a
    recorded split has no finding that changed.
    """
    from repowise.core.pipeline.incremental import _history_findings_moved, _numbers_moved

    stored = _metric()
    assert stored.structure_deduction is None
    (refreshed,) = _run(git_meta={"prior_defect_count": 0}, findings=[])
    assert _numbers_moved(refreshed, stored)
    assert not _history_findings_moved(refreshed, [])


def test_an_unchanged_file_keeps_its_finding_identity() -> None:
    """Re-deriving the same history must not churn ids or ages."""
    from repowise.core.pipeline.incremental import _history_findings_moved

    structural = _finding("god_class", Severity.CRITICAL, health_impact=2.26)
    (refreshed,) = _run(git_meta={"prior_defect_count": 0}, findings=[structural])
    assert not _history_findings_moved(refreshed, refreshed.findings)


def test_stored_findings_rebuild_exactly() -> None:
    """The rescore replays stored findings, so the lift has to be lossless.

    ``coverage_gradient`` is the one marker whose magnitude is continuous rather
    than banded, and it writes that magnitude into its own details.
    """
    rebuilt = as_biomarker_result(
        _finding("coverage_gradient", Severity.LOW, details={"deduction": 3.2})
    )
    assert rebuilt.biomarker_type == "coverage_gradient"
    assert rebuilt.severity == Severity.LOW
    assert rebuilt.deduction == 3.2
    assert as_biomarker_result(_finding("god_class")).deduction is None


def test_rebuild_reads_a_stored_row_shape() -> None:
    """Findings arrive as ORM rows after persistence, with details as JSON text."""

    class _Row:
        biomarker_type = "coverage_gradient"
        severity = "low"
        function_name = None
        line_start = None
        line_end = None
        details_json = json.dumps({"deduction": 2.5})
        reason = "half the lines uncovered"

    rebuilt = as_biomarker_result(_Row())
    assert rebuilt.deduction == 2.5
    assert rebuilt.reason == "half the lines uncovered"
