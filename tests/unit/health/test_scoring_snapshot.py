"""Snapshot tests guarding scoring stability across refactors.

Locks the published per-category caps, per-severity deductions, and the
biomarker → category mapping. A change to any of these tables shifts every
file's score on every repo using repowise, so this test exists to force
the reviewer to acknowledge the impact before landing.

If a deliberate retune lands, regenerate the snapshot by updating the
``_EXPECTED_*`` constants below in the same PR — never silently.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    _BIOMARKER_CATEGORY,
    _SEVERITY_DEDUCTION,
    CATEGORY_CAPS,
    score_file,
)

_EXPECTED_CATEGORY_CAPS = {
    "structural_complexity": 3.5,
    "size_and_complexity": 2.0,
    "duplication": 1.5,
    "test_coverage": 2.0,
    "organizational": 1.0,
}

_EXPECTED_SEVERITY_DEDUCTION = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

_EXPECTED_BIOMARKER_CATEGORY = {
    "brain_method": "structural_complexity",
    "nested_complexity": "structural_complexity",
    "bumpy_road": "structural_complexity",
    "complex_method": "size_and_complexity",
    "large_method": "size_and_complexity",
    "primitive_obsession": "size_and_complexity",
    "dry_violation": "duplication",
    "untested_hotspot": "test_coverage",
    "coverage_gap": "test_coverage",
    "developer_congestion": "organizational",
    "knowledge_loss": "organizational",
}


def test_category_caps_locked():
    assert CATEGORY_CAPS == _EXPECTED_CATEGORY_CAPS


def test_severity_deductions_locked():
    assert _SEVERITY_DEDUCTION == _EXPECTED_SEVERITY_DEDUCTION


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
    # Math: structural cat = 2.0 + 1.2 = 3.2 (under 3.5 cap)
    #       size_and_complexity = 0.7 (under 2.0 cap)
    #       coverage = 1.2 (under 2.0 cap)
    #       organizational = 0.3 (under 1.0 cap)
    #       total deduction = 3.2 + 0.7 + 1.2 + 0.3 = 5.4 → 10 - 5.4 = 4.6
    assert score == 4.6


def test_category_cap_clamps_score():
    """Many critical structural findings should not exceed the -3.5 cap."""
    findings = [_result("brain_method", Severity.CRITICAL) for _ in range(10)]
    score, _ = score_file(findings)
    # Cap at -3.5, so floor on this category alone is 6.5.
    assert score == 6.5
