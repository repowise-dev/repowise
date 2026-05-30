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
    _score_breakdown_from_findings,
)


def _finding(biomarker_type, severity, health_impact, *, details=None, fid="x"):
    return SimpleNamespace(
        id=fid,
        biomarker_type=biomarker_type,
        severity=severity,
        health_impact=health_impact,
        function_name=None,
        reason="",
        details=details or {},
    )


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
