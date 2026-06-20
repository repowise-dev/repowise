"""Snapshot tests guarding scoring stability across refactors.

Locks the published per-category caps, per-severity deductions, the
per-biomarker weight multipliers, and the biomarker -> category mapping.
A change to any of these tables shifts every file's score on every repo
using repowise, so this test exists to force the reviewer to acknowledge
the impact before landing.

If a deliberate retune lands, regenerate the snapshot by updating the
``_EXPECTED_*`` constants below in the same PR - never silently.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    _BIOMARKER_CATEGORY,
    _BIOMARKER_WEIGHT_MULTIPLIER,
    _SEVERITY_DEDUCTION,
    CATEGORY_CAPS,
    score_file,
)

_EXPECTED_CATEGORY_CAPS = {
    "organizational": 3.5,
    "structural_complexity": 2.5,
    "test_coverage": 2.0,
    # Continuous coverage-gradient deduction - own capped category so it stays
    # additive to (and bounded independently of) the binary coverage gates.
    "test_coverage_gradient": 2.0,
    "size_and_complexity": 1.5,
    "duplication": 1.0,
    "test_quality": 0.5,
    # Error-handling anti-patterns - advisory maintainability signal in its
    # own bounded category (AUC-neutral on the T0 benchmark by design).
    "error_handling": 0.5,
}

_EXPECTED_SEVERITY_DEDUCTION = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

# Defect-calibrated offline (2026-05-29) against a 15-repo / 5-language corpus,
# scoring each file at T0 with an L2-logistic + explicit NLOC control. These are
# learned constants, not hand priors - regenerate via
# local-stash/calibrate_health_weights.py if the corpus changes. See the comment
# block on scoring._BIOMARKER_WEIGHT_MULTIPLIER for the "balanced" mapping policy.
_EXPECTED_BIOMARKER_WEIGHT_MULTIPLIER = {
    # calibrated predictors
    "co_change_scatter": 1.8,
    "change_entropy": 1.51,
    "ownership_risk": 1.38,
    "nested_complexity": 1.34,
    "complex_conditional": 1.33,
    "large_method": 1.25,
    "complex_method": 1.21,
    "function_hotspot": 1.16,
    "god_class": 1.13,
    # prior-defect history - neutral weight: interpretable finding, calibrated
    # coef ~0 (redundant with change_entropy/churn), Popt gain within noise.
    "prior_defect": 1.0,
    # kept at prior (benchmark could not fairly measure)
    "untested_hotspot": 1.3,
    "churn_risk": 1.2,
    "code_age_volatility": 1.1,
    # floored - fired widely but weak/non-predictive at T0
    "developer_congestion": 0.5,
    "low_cohesion": 0.5,
    "brain_method": 0.5,
    "bumpy_road": 0.5,
    "primitive_obsession": 0.5,
    "dry_violation": 0.5,
    "knowledge_loss": 0.4,
    # Error-handling anti-patterns - maintainability flag excluded from the
    # calibration roster; floored weight keeps LOW findings at 0.15 each.
    "error_handling": 0.5,
    # Governance biomarkers (informational - surfaced as findings, not fed back
    # into the score pass, which has already run upstream).
    "contradictory_decision": 1.0,
    "stale_governance": 0.9,
    "ungoverned_hotspot": 0.7,
}

_EXPECTED_BIOMARKER_CATEGORY = {
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
    # Phase 4B governance biomarkers.
    "ungoverned_hotspot": "organizational",
    "stale_governance": "organizational",
    "contradictory_decision": "organizational",
}


def test_category_caps_locked():
    assert CATEGORY_CAPS == _EXPECTED_CATEGORY_CAPS


def test_severity_deductions_locked():
    assert _SEVERITY_DEDUCTION == _EXPECTED_SEVERITY_DEDUCTION


def test_biomarker_weight_multipliers_locked():
    assert _BIOMARKER_WEIGHT_MULTIPLIER == _EXPECTED_BIOMARKER_WEIGHT_MULTIPLIER


def test_biomarker_to_category_locked():
    assert _BIOMARKER_CATEGORY == _EXPECTED_BIOMARKER_CATEGORY


def _result(biomarker: str, severity: Severity) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=biomarker,
        severity=severity,
        function_name=None,
        line_start=None,
        line_end=None,
        details={},
        reason="",
    )


def test_known_fixture_score_is_stable():
    """A handcrafted finding set should produce a known, fixed score."""
    findings = [
        _result("brain_method", Severity.CRITICAL),
        _result("nested_complexity", Severity.HIGH),
        _result("complex_method", Severity.MEDIUM),
        _result("untested_hotspot", Severity.HIGH),
        _result("knowledge_loss", Severity.LOW),
    ]
    scores, _ = score_file(findings)
    # Math (defect-calibrated weights):
    #   structural   = brain(2.0*0.5) + nested(1.2*1.34) = 1.0 + 1.608 = 2.608 -> capped at 2.5
    #   size_and_cx  = complex_method 0.7 * 1.21 = 0.847   (under 1.5 cap)
    #   coverage     = untested_hotspot 1.2 * 1.3 = 1.56   (under 2.0 cap)
    #   organizational = knowledge_loss 0.3 * 0.4 = 0.12   (under 3.5 cap)
    #   total deduction = 2.5 + 0.847 + 1.56 + 0.12 = 5.027 -> 10 - 5.027 = 4.973
    assert scores["defect"] == 4.973


def test_category_cap_clamps_score():
    """Many critical structural findings should not exceed the -2.5 cap."""
    findings = [_result("brain_method", Severity.CRITICAL) for _ in range(10)]
    scores, _ = score_file(findings)
    # Cap at -2.5, so floor on this category alone is 7.5.
    assert scores["defect"] == 7.5


def test_continuous_deduction_override_is_used_and_capped():
    """A ``deduction`` override replaces the severity table and is category-capped.

    ``coverage_gradient`` deducts 4.0 x uncovered_fraction in its own
    ``test_coverage_gradient`` category (cap 2.0). At 25% uncovered the raw
    deduction is 1.0 (under the cap); at 80% uncovered it is 3.2 -> clamped to
    the 2.0 cap.
    """
    quarter = BiomarkerResult(
        biomarker_type="coverage_gradient",
        severity=Severity.LOW,
        function_name=None,
        line_start=None,
        line_end=None,
        details={},
        reason="",
        deduction=4.0 * 0.25,
    )
    scores, ded = score_file([quarter])
    assert scores["defect"] == 9.0  # 10 - 1.0
    assert ded == [1.0]

    deep = BiomarkerResult(
        biomarker_type="coverage_gradient",
        severity=Severity.HIGH,
        function_name=None,
        line_start=None,
        line_end=None,
        details={},
        reason="",
        deduction=4.0 * 0.80,
    )
    scores2, ded2 = score_file([deep])
    assert scores2["defect"] == 8.0  # 10 - 2.0 (cap)
    assert ded2 == [2.0]


def test_error_handling_cap_bounds_stream():
    """error_handling findings deduct 0.15 each (LOW 0.3 x 0.5 weight) in
    their own advisory category, capped at 0.5/file regardless of count."""
    two = [_result("error_handling", Severity.LOW) for _ in range(2)]
    scores, deductions = score_file(two)
    assert scores["defect"] == 9.7  # 10 - 2 * 0.15
    assert deductions == [0.15, 0.15]

    many = [_result("error_handling", Severity.LOW) for _ in range(10)]
    scores2, _ = score_file(many)
    assert scores2["defect"] == 9.5  # 10 * 0.15 = 1.5 raw -> clamped to the 0.5 cap


def test_organizational_cap_bounds_stream():
    """The organizational cap (-3.5) bounds a high-volume finding stream. With
    developer_congestion defect-calibrated down to 0.5 (it was a HEAD-leakage
    artifact), three CRITICALs deduct under the cap rather than saturating it."""
    findings = [_result("developer_congestion", Severity.CRITICAL) for _ in range(3)]
    scores, _ = score_file(findings)
    # 3 * 2.0 * 0.5 = 3.0 weighted (< 3.5 cap) -> score = 7.0
    assert scores["defect"] == 7.0
