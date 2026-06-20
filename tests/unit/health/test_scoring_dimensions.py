"""Three-signal split: the golden defect guarantee + maintainability scoring.

The load-bearing guardrail of the dimension split is that the ``defect``
dimension reproduces the pre-split single score EXACTLY. ``_legacy_score_file``
below is a FROZEN copy of the scoring algorithm and its three tables as they
stood before the split. ``test_defect_dimension_matches_legacy_golden`` asserts
``score_file(...)["defect"]`` equals that frozen reference across a broad fixture
set. If it ever drifts, the split has corrupted the calibrated, surfaced score
and the change is wrong - do NOT update the golden to match; fix the regression.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    DIMENSIONS,
    attach_impacts,
    biomarker_dimension,
    dimensions_for,
    score_file,
)

# ---------------------------------------------------------------------------
# Frozen legacy reference (do NOT import the live tables - copy them so a live
# retune can never silently move the golden).
# ---------------------------------------------------------------------------

_LEGACY_CATEGORY_CAPS = {
    "organizational": 3.5,
    "structural_complexity": 2.5,
    "test_coverage": 2.0,
    "test_coverage_gradient": 2.0,
    "size_and_complexity": 1.5,
    "duplication": 1.0,
    "test_quality": 0.5,
    "error_handling": 0.5,
}

_LEGACY_SEVERITY_DEDUCTION = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

_LEGACY_WEIGHT_MULTIPLIER = {
    "co_change_scatter": 1.8,
    "change_entropy": 1.51,
    "ownership_risk": 1.38,
    "nested_complexity": 1.34,
    "complex_conditional": 1.33,
    "large_method": 1.25,
    "complex_method": 1.21,
    "function_hotspot": 1.16,
    "god_class": 1.13,
    "prior_defect": 1.0,
    "untested_hotspot": 1.3,
    "churn_risk": 1.2,
    "code_age_volatility": 1.1,
    "developer_congestion": 0.5,
    "low_cohesion": 0.5,
    "brain_method": 0.5,
    "bumpy_road": 0.5,
    "primitive_obsession": 0.5,
    "dry_violation": 0.5,
    "knowledge_loss": 0.4,
    "error_handling": 0.5,
    "contradictory_decision": 1.0,
    "stale_governance": 0.9,
    "ungoverned_hotspot": 0.7,
}

_LEGACY_CATEGORY = {
    "brain_method": "structural_complexity",
    "low_cohesion": "structural_complexity",
    "god_class": "structural_complexity",
    "nested_complexity": "structural_complexity",
    "bumpy_road": "structural_complexity",
    "complex_conditional": "structural_complexity",
    "complex_method": "size_and_complexity",
    "large_method": "size_and_complexity",
    "primitive_obsession": "size_and_complexity",
    "dry_violation": "duplication",
    "untested_hotspot": "test_coverage",
    "coverage_gap": "test_coverage",
    "coverage_gradient": "test_coverage_gradient",
    "developer_congestion": "organizational",
    "knowledge_loss": "organizational",
    "hidden_coupling": "organizational",
    "function_hotspot": "organizational",
    "code_age_volatility": "organizational",
    "ownership_risk": "organizational",
    "churn_risk": "organizational",
    "change_entropy": "organizational",
    "co_change_scatter": "organizational",
    "prior_defect": "organizational",
    "large_assertion_block": "test_quality",
    "duplicated_assertion_block": "test_quality",
    "error_handling": "error_handling",
    "ungoverned_hotspot": "organizational",
    "stale_governance": "organizational",
    "contradictory_decision": "organizational",
}


def _legacy_score_file(results: list[BiomarkerResult]) -> float:
    """The pre-split algorithm, verbatim, against the frozen tables above."""
    raw: dict[str, float] = {}
    for r in results:
        cat = _LEGACY_CATEGORY.get(r.biomarker_type, "size_and_complexity")
        base = (
            r.deduction
            if r.deduction is not None
            else _LEGACY_SEVERITY_DEDUCTION.get(r.severity, 0.5)
        )
        weighted = base * _LEGACY_WEIGHT_MULTIPLIER.get(r.biomarker_type, 1.0)
        raw[cat] = raw.get(cat, 0.0) + weighted

    total = 0.0
    for cat, cat_sum in raw.items():
        cap = _LEGACY_CATEGORY_CAPS.get(cat, 1.0)
        total += min(cat_sum, cap)
    return max(1.0, min(10.0, 10.0 - total))


def _r(name: str, severity: Severity, *, deduction: float | None = None) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=severity,
        function_name=None,
        line_start=None,
        line_end=None,
        details={},
        reason="",
        deduction=deduction,
    )


# A broad, deterministic spread of fixtures: empty, single-category saturation,
# cross-category mixes, floored smells, structural duals, continuous overrides,
# and an unknown biomarker (default category/weight path).
_FIXTURES: list[list[BiomarkerResult]] = [
    [],
    [_r("complex_method", Severity.CRITICAL) for _ in range(20)],
    [_r("brain_method", Severity.CRITICAL), _r("nested_complexity", Severity.CRITICAL)],
    [
        _r("brain_method", Severity.CRITICAL),
        _r("nested_complexity", Severity.HIGH),
        _r("complex_method", Severity.MEDIUM),
        _r("untested_hotspot", Severity.HIGH),
        _r("knowledge_loss", Severity.LOW),
    ],
    [_r("error_handling", Severity.LOW) for _ in range(10)],
    [_r("developer_congestion", Severity.CRITICAL) for _ in range(3)],
    [
        _r("co_change_scatter", Severity.HIGH),
        _r("change_entropy", Severity.MEDIUM),
        _r("ownership_risk", Severity.HIGH),
        _r("dry_violation", Severity.MEDIUM),
        _r("primitive_obsession", Severity.HIGH),
        _r("god_class", Severity.CRITICAL),
        _r("large_method", Severity.HIGH),
        _r("low_cohesion", Severity.MEDIUM),
    ],
    [_r("coverage_gradient", Severity.LOW, deduction=4.0 * 0.8)],
    [_r("some_unregistered_biomarker", Severity.HIGH)],
]


def test_defect_dimension_matches_legacy_golden():
    """THE GATE: scores["defect"] == the frozen pre-split score for every fixture."""
    for fixture in _FIXTURES:
        scores, _ = score_file(fixture)
        assert scores["defect"] == _legacy_score_file(fixture)


def test_defect_deductions_unchanged():
    """The returned per-finding deductions still drive defect-based health_impact."""
    fixture = _FIXTURES[3]
    _, deductions = score_file(fixture)
    # Parallel to the input and non-negative.
    assert len(deductions) == len(fixture)
    assert all(d >= 0 for d in deductions)


def test_score_file_returns_all_dimensions():
    scores, _ = score_file([_r("brain_method", Severity.HIGH)])
    assert set(scores) == set(DIMENSIONS)


def test_performance_is_none_until_detectors_land():
    """No performance biomarkers exist yet -> the dimension is null, not 10.0."""
    for fixture in _FIXTURES:
        scores, _ = score_file(fixture)
        assert scores["performance"] is None


def test_clean_file_is_ten_in_every_active_dimension():
    scores, _ = score_file([])
    assert scores["defect"] == 10.0
    assert scores["maintainability"] == 10.0


# ---------------------------------------------------------------------------
# Maintainability dimension
# ---------------------------------------------------------------------------


def test_floored_smell_is_full_weight_in_maintainability():
    """low_cohesion is floored to 0.5 in defect but full (1.0) in maintainability."""
    findings = [_r("low_cohesion", Severity.CRITICAL)]
    scores, _ = score_file(findings)
    # defect: 2.0 * 0.5 = 1.0 -> 9.0
    assert scores["defect"] == 9.0
    # maintainability: 2.0 * 1.0 = 2.0 in structural_complexity (cap 4.0) -> 8.0
    assert scores["maintainability"] == 8.0


def test_maintainability_ignores_pure_defect_biomarkers():
    """A biomarker that only feeds defect leaves maintainability untouched."""
    findings = [_r("change_entropy", Severity.CRITICAL)]
    assert "maintainability" not in dimensions_for("change_entropy")
    scores, _ = score_file(findings)
    assert scores["maintainability"] == 10.0
    assert scores["defect"] < 10.0


def test_structural_dual_counts_toward_both_dimensions():
    findings = [_r("god_class", Severity.HIGH)]
    assert dimensions_for("god_class") == {"defect", "maintainability"}
    scores, _ = score_file(findings)
    assert scores["defect"] < 10.0
    assert scores["maintainability"] < 10.0


def test_maintainability_accumulates_across_findings():
    """Two structural maintainability smells accumulate (under the 4.0 cap)."""
    findings = [
        _r("low_cohesion", Severity.HIGH),  # 1.2 * 1.0
        _r("brain_method", Severity.HIGH),  # 1.2 * 1.0
    ]
    scores, _ = score_file(findings)
    # structural_complexity maintainability: 1.2 + 1.2 = 2.4 (< 4.0 cap) -> 7.6
    assert scores["maintainability"] == 7.6


def test_maintainability_structural_cap_bounds_stream():
    """Many critical structural smells saturate the 4.0 maintainability cap."""
    findings = [_r("brain_method", Severity.CRITICAL) for _ in range(10)]
    scores, _ = score_file(findings)
    # 10 * 2.0 = 20 raw -> clamped to the 4.0 structural cap -> 6.0
    assert scores["maintainability"] == 6.0


def test_maintainability_categories_capped_independently():
    """Each maintainability category is bounded on its own budget."""
    findings = [
        _r("brain_method", Severity.CRITICAL),  # structural, capped at 4.0
        _r("brain_method", Severity.CRITICAL),
        _r("brain_method", Severity.CRITICAL),
        _r("dry_violation", Severity.CRITICAL),  # duplication, capped at 2.0
        _r("dry_violation", Severity.CRITICAL),
        _r("error_handling", Severity.CRITICAL),  # error_handling, capped at 2.0
        _r("error_handling", Severity.CRITICAL),
    ]
    scores, _ = score_file(findings)
    # structural 6.0->4.0 cap, duplication 4.0->2.0 cap, error_handling 4.0->2.0 cap
    # total = 4.0 + 2.0 + 2.0 = 8.0 -> 10 - 8 = 2.0
    assert scores["maintainability"] == 2.0


# ---------------------------------------------------------------------------
# Finding "home" dimension
# ---------------------------------------------------------------------------


def test_biomarker_home_dimension():
    assert biomarker_dimension("low_cohesion") == "maintainability"
    assert biomarker_dimension("error_handling") == "maintainability"
    # Structural duals home to defect (their primary, calibrated role).
    assert biomarker_dimension("god_class") == "defect"
    # Calibrated predictors home to defect.
    assert biomarker_dimension("change_entropy") == "defect"
    # Unknown defaults to defect.
    assert biomarker_dimension("mystery") == "defect"


def test_attach_impacts_tags_finding_dimension():
    results = [_r("low_cohesion", Severity.HIGH), _r("change_entropy", Severity.HIGH)]
    _, deductions = score_file(results)
    findings = attach_impacts(results, deductions)
    assert findings[0].dimension == "maintainability"
    assert findings[1].dimension == "defect"
