"""Unit tests for the score-breakdown helper in the code-health router.

Pins the Task-12.5 fix: the breakdown reads each finding's STORED
``health_impact`` (the continuous, already-weighted-and-capped value) rather
than recomputing from the severity band, so the coverage gradient is surfaced
faithfully and the breakdown reproduces the file's score.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import biomarker_weight, severity_deduction
from repowise.server.routers.code_health import (
    _finding_base_deduction,
    _leads_by_file,
    _primary_and_magnitude,
    _score_breakdown_from_findings,
)


def _finding(
    biomarker_type, severity, health_impact, *, details=None, fid="x", reason="", file_path="f.py"
):
    return SimpleNamespace(
        id=fid,
        file_path=file_path,
        biomarker_type=biomarker_type,
        severity=severity,
        health_impact=health_impact,
        function_name=None,
        reason=reason,
        details=details or {},
    )


def test_primary_and_magnitude_picks_worst_and_sums_impact() -> None:
    findings = [
        _finding("complex_method", Severity.HIGH, 0.8, reason="ccn 15", fid="a"),
        _finding("god_object", Severity.CRITICAL, 3.5, reason="1200-line class", fid="b"),
        _finding("deep_nesting", Severity.MEDIUM, 0.7, reason="nests 6 deep", fid="c"),
    ]
    lead = _primary_and_magnitude(findings)
    # Dominant cause = the single worst finding, not the first one.
    assert lead["primary_biomarker"] == "god_object"
    assert lead["primary_reason"] == "1200-line class"
    # Magnitude = summed (pre-floor) impact = 0.8 + 3.5 + 0.7.
    assert abs(lead["total_deduction"] - 5.0) < 1e-6


def test_primary_and_magnitude_empty_is_all_null() -> None:
    assert _primary_and_magnitude([]) == {
        "primary_biomarker": None,
        "primary_reason": None,
        "total_deduction": None,
    }


def test_leads_by_file_groups_per_path() -> None:
    findings = [
        _finding("complex_method", Severity.HIGH, 1.0, fid="a", file_path="a.py"),
        _finding("god_object", Severity.CRITICAL, 4.0, fid="b", file_path="b.py"),
        _finding("deep_nesting", Severity.LOW, 0.5, fid="c", file_path="b.py"),
    ]
    leads = _leads_by_file(findings)
    assert leads["a.py"]["primary_biomarker"] == "complex_method"
    assert leads["b.py"]["primary_biomarker"] == "god_object"
    assert abs(leads["b.py"]["total_deduction"] - 4.5) < 1e-6


def test_base_deduction_prefers_continuous_override() -> None:
    # coverage_gradient records its continuous base in details["deduction"].
    f = _finding("coverage_gradient", Severity.MEDIUM, 1.2, details={"deduction": 1.6})
    assert _finding_base_deduction(f) == 1.6
    # Without an override, falls back to the severity band.
    g = _finding("complex_method", Severity.HIGH, 0.5)
    assert _finding_base_deduction(g) == severity_deduction(Severity.HIGH)


def test_breakdown_uses_stored_impact_and_reproduces_score() -> None:
    findings = [
        _finding("coverage_gradient", Severity.MEDIUM, 1.2, details={"deduction": 1.6}, fid="a"),
        _finding("complex_method", Severity.HIGH, 0.8, fid="b"),
    ]
    out = _score_breakdown_from_findings(findings)

    # total = sum of stored impacts; score = 10 - total.
    assert abs(out["total_deduction"] - 2.0) < 1e-6
    assert abs(out["score"] - 8.0) < 1e-6

    cats = {c["category"]: c for c in out["categories"]}
    # The continuous gradient category is present and applied == stored impact.
    grad = cats["test_coverage_gradient"]
    assert abs(grad["applied_deduction"] - 1.2) < 1e-6
    gf = grad["findings"][0]
    assert abs(gf["applied_impact"] - 1.2) < 1e-6
    # Raw = continuous base x biomarker weight (NOT a severity-band proxy).
    assert abs(gf["raw_impact"] - 1.6 * biomarker_weight("coverage_gradient")) < 1e-6
    # Empty categories are omitted.
    assert all(c["finding_count"] > 0 for c in out["categories"])


def test_breakdown_flags_capped_category() -> None:
    # Two organizational findings whose raw weighted sum exceeds the 3.5 cap,
    # but the stored applied impacts are held at the cap.
    findings = [
        _finding("co_change_scatter", Severity.CRITICAL, 1.75, fid="a"),
        _finding("change_entropy", Severity.CRITICAL, 1.75, fid="b"),
    ]
    out = _score_breakdown_from_findings(findings)
    org = next(c for c in out["categories"] if c["category"] == "organizational")
    # raw (uncapped) should exceed applied (held at cap) → capped flag set.
    assert org["raw_deduction"] > org["applied_deduction"]
    assert org["capped"] is True
