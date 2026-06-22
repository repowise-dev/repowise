"""Unit tests for ``scoring.score_file`` and KPI helpers."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import HealthFileMetricData, Severity
from repowise.core.analysis.health.scoring import (
    CATEGORY_CAPS,
    compute_kpis,
    remap_severities,
    score_file,
)


def _r(name: str, severity: Severity) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=severity,
        function_name=None,
        line_start=1,
        line_end=10,
        details={},
        reason="",
    )


def test_remap_severities_demotes_and_lowers_deduction():
    # A HIGH complex_method deducts more than a LOW one; remapping to LOW
    # must raise the resulting score (smaller deduction).
    high = [_r("complex_method", Severity.HIGH)]
    base_score, _ = score_file(high)
    remapped = remap_severities(high, {"complex_method": Severity.LOW})
    low_score, _ = score_file(remapped)
    assert low_score["defect"] > base_score["defect"]
    # Original list is not mutated in place.
    assert high[0].severity is Severity.HIGH


def test_remap_severities_noop_without_overrides():
    results = [_r("complex_method", Severity.HIGH)]
    assert remap_severities(results, None) is results
    assert remap_severities(results, {}) is results


def test_remap_severities_skips_continuous_deduction_findings():
    # coverage_gradient-style findings carry a ``deduction`` override; their
    # magnitude is not severity-derived, so a remap must not touch them.
    r = BiomarkerResult(
        biomarker_type="coverage_gradient",
        severity=Severity.HIGH,
        function_name=None,
        line_start=1,
        line_end=10,
        details={},
        reason="",
        deduction=2.0,
    )
    out = remap_severities([r], {"coverage_gradient": Severity.LOW})
    assert out[0] is r  # untouched
    assert out[0].deduction == 2.0


def test_score_clean_file_is_ten():
    scores, deductions = score_file([])
    assert scores["defect"] == 10.0
    assert deductions == []


def test_score_clamps_floor_at_one():
    # Twenty critical findings in the same category — should hit the cap
    # but never go below 1.0.
    results = [_r("complex_method", Severity.CRITICAL) for _ in range(20)]
    scores, _ = score_file(results)
    assert scores["defect"] >= 1.0
    # Cap on size_and_complexity is 1.5 (post-recalibration) → score 8.5.
    assert scores["defect"] == 8.5


def test_category_cap_applied():
    # Two structural-complexity hits that together would deduct 4.0 raw,
    # but the category cap is 3.5.
    results = [
        _r("brain_method", Severity.CRITICAL),  # 2.0 raw
        _r("nested_complexity", Severity.CRITICAL),  # 2.0 raw
    ]
    scores, _ = score_file(results)
    assert scores["defect"] == 10.0 - CATEGORY_CAPS["structural_complexity"]


def test_compute_kpis_uses_nloc_weighting():
    metrics = [
        HealthFileMetricData(
            "a.py", score=5.0, max_ccn=1, max_nesting=1, nloc=100, has_test_file=False
        ),
        HealthFileMetricData(
            "b.py", score=10.0, max_ccn=1, max_nesting=1, nloc=10, has_test_file=False
        ),
    ]
    kpis = compute_kpis(metrics, hotspot_paths=set())
    # Weighted avg = (5*100 + 10*10) / 110 ≈ 5.45
    assert abs(kpis["average_health"] - 5.45) < 0.02
    assert kpis["worst_performer_path"] == "a.py"


def test_compute_kpis_empty_returns_defaults():
    kpis = compute_kpis([], hotspot_paths=set())
    assert kpis["average_health"] == 10.0
    assert kpis["file_count"] == 0
    assert kpis["maintainability_average"] is None
    assert kpis["performance_average"] is None


def test_compute_kpis_maintainability_average_nloc_weighted():
    metrics = [
        HealthFileMetricData(
            "a.py",
            score=5.0,
            max_ccn=1,
            max_nesting=1,
            nloc=100,
            has_test_file=False,
            maintainability_score=6.0,
        ),
        HealthFileMetricData(
            "b.py",
            score=10.0,
            max_ccn=1,
            max_nesting=1,
            nloc=10,
            has_test_file=False,
            maintainability_score=9.0,
        ),
    ]
    kpis = compute_kpis(metrics, hotspot_paths={"a.py"})
    # Weighted avg = (6*100 + 9*10) / 110 ≈ 6.27
    assert abs(kpis["maintainability_average"] - 6.27) < 0.02
    # Hotspot restricted to a.py -> its own maintainability score.
    assert kpis["maintainability_hotspot"] == 6.0


def test_compute_kpis_maintainability_none_when_unscored():
    """Files predating the split (no maintainability_score) -> None, not 10.0."""
    metrics = [
        HealthFileMetricData(
            "a.py", score=5.0, max_ccn=1, max_nesting=1, nloc=100, has_test_file=False
        ),
    ]
    kpis = compute_kpis(metrics, hotspot_paths=set())
    assert kpis["maintainability_average"] is None


def test_compute_kpis_performance_average_nloc_weighted():
    metrics = [
        HealthFileMetricData(
            "a.py",
            score=5.0,
            max_ccn=1,
            max_nesting=1,
            nloc=100,
            has_test_file=False,
            performance_score=8.0,
        ),
        HealthFileMetricData(
            "b.py",
            score=10.0,
            max_ccn=1,
            max_nesting=1,
            nloc=10,
            has_test_file=False,
            performance_score=10.0,
        ),
    ]
    kpis = compute_kpis(metrics, hotspot_paths={"a.py"})
    # Weighted avg = (8*100 + 10*10) / 110 ≈ 8.18
    assert abs(kpis["performance_average"] - 8.18) < 0.02
    # Hotspot restricted to a.py -> its own performance score.
    assert kpis["performance_hotspot"] == 8.0


def test_compute_kpis_performance_none_when_unscored():
    """Files predating the perf detectors (no performance_score) -> None, not 10.0."""
    metrics = [
        HealthFileMetricData(
            "a.py", score=5.0, max_ccn=1, max_nesting=1, nloc=100, has_test_file=False
        ),
    ]
    kpis = compute_kpis(metrics, hotspot_paths=set())
    assert kpis["performance_average"] is None
