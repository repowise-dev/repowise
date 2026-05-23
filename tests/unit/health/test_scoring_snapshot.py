"""Snapshot tests guarding scoring stability across refactors.

Locks the published per-category caps, per-severity deductions, the
per-biomarker weight multipliers, and the biomarker → category mapping.
A change to any of these tables shifts every file's score on every repo
using repowise, so this test exists to force the reviewer to acknowledge
the impact before landing.

If a deliberate retune lands, regenerate the snapshot by updating the
``_EXPECTED_*`` constants below in the same PR — never silently.
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
    "size_and_complexity": 1.5,
    "duplication": 1.0,
}

_EXPECTED_SEVERITY_DEDUCTION = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

_EXPECTED_BIOMARKER_WEIGHT_MULTIPLIER = {
    "developer_congestion": 1.5,
    "untested_hotspot": 1.3,
    "function_hotspot": 1.2,
    "hidden_coupling": 1.0,
    "knowledge_loss": 0.4,
}

_EXPECTED_BIOMARKER_CATEGORY = {
    "brain_method": "structural_complexity",
    "nested_complexity": "structural_complexity",
    "bumpy_road": "structural_complexity",
    "complex_conditional": "structural_complexity",
    "complex_method": "size_and_complexity",
    "large_method": "size_and_complexity",
    "primitive_obsession": "size_and_complexity",
    "dry_violation": "duplication",
    "untested_hotspot": "test_coverage",
    "coverage_gap": "test_coverage",
    "developer_congestion": "organizational",
    "knowledge_loss": "organizational",
    "hidden_coupling": "organizational",
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
    score, _ = score_file(findings)
    # Math (post-recalibration):
    #   structural   = 2.0 + 1.2 = 3.2 → capped at 2.5
    #   size_and_cx  = 0.7        (under 1.5 cap)
    #   coverage     = 1.2 * 1.3 = 1.56 (under 2.0 cap)
    #   organizational = 0.3 * 0.4 = 0.12 (under 3.5 cap)
    #   total deduction = 2.5 + 0.7 + 1.56 + 0.12 = 4.88 → 10 - 4.88 = 5.12
    assert score == 5.12


def test_category_cap_clamps_score():
    """Many critical structural findings should not exceed the -2.5 cap."""
    findings = [_result("brain_method", Severity.CRITICAL) for _ in range(10)]
    score, _ = score_file(findings)
    # Cap at -2.5, so floor on this category alone is 7.5.
    assert score == 7.5


def test_organizational_cap_now_dominant():
    """Recalibration lifts organizational from -1.0 to -3.5 — verify a high-volume
    developer_congestion stream now lands a real dent instead of being suppressed."""
    findings = [_result("developer_congestion", Severity.CRITICAL) for _ in range(3)]
    score, _ = score_file(findings)
    # 3 * 2.0 * 1.5 = 9.0 weighted, capped at 3.5 → score = 6.5
    assert score == 6.5
