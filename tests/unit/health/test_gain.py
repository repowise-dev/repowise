"""A fix's gain is the file rescored without it, never a sum of health_impact."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.gain import removal_gain, structure_freed
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.opportunity import _recoverable
from repowise.core.analysis.health.scoring import attach_impacts, score_file


def _result(marker: str, severity: Severity, function: str | None = None, **details):
    return BiomarkerResult(
        biomarker_type=marker,
        severity=severity,
        function_name=function,
        line_start=1,
        line_end=2,
        details=details,
        reason="",
    )


def _scored(*results: BiomarkerResult):
    """Findings as the index stores them, and the file's score."""
    scores, deductions = score_file(list(results))
    return attach_impacts(list(results), deductions), scores["defect"]


def test_saturated_category_gains_almost_nothing_per_marker():
    findings, _ = _scored(
        *(_result("complex_method", Severity.CRITICAL, f"f{i}", ccn=60) for i in range(8))
    )
    one = findings[0]
    assert one.health_impact > 0.5
    assert removal_gain(findings, [(one, 1.0)]) < 0.01


def test_floored_file_gains_nothing_from_one_fix():
    findings, score = _scored(
        _result("co_change_scatter", Severity.CRITICAL),
        _result("change_entropy", Severity.CRITICAL),
        *(
            _result(marker, Severity.CRITICAL, f"g{i}", ccn=80, nloc=600, max_nesting=12)
            for i, marker in enumerate(
                ["complex_method", "large_method", "nested_complexity", "complex_conditional"] * 3
            )
        ),
    )
    assert score == 1.0
    assert sum(f.health_impact for f in findings) > 9.0
    assert all(removal_gain(findings, [(f, 1.0)]) == 0.0 for f in findings)


def test_unsaturated_markers_gain_their_impact():
    findings, _ = _scored(
        _result("complex_method", Severity.MEDIUM, "a"),
        _result("dry_violation", Severity.LOW),
    )
    for f in findings:
        assert removal_gain(findings, [(f, 1.0)]) == pytest.approx(f.health_impact, abs=2e-3)
    both = removal_gain(findings, [(f, 1.0) for f in findings])
    assert both == pytest.approx(sum(f.health_impact for f in findings), abs=2e-3)


def test_code_fix_releases_history_held_at_its_cap():
    findings, _ = _scored(
        _result("complex_method", Severity.MEDIUM, "a"),
        _result("co_change_scatter", Severity.CRITICAL),
    )
    code = findings[0]
    # History sits at 1.0 + structure; removing the structure lowers that cap too.
    assert removal_gain(findings, [(code, 1.0)]) == pytest.approx(2 * code.health_impact, abs=2e-3)


def test_partial_share_gains_part_of_the_whole():
    findings, _ = _scored(_result("complex_method", Severity.HIGH, "a"))
    whole = removal_gain(findings, [(findings[0], 1.0)])
    half = removal_gain(findings, [(findings[0], 0.5)])
    assert 0 < half < whole
    assert removal_gain(findings, []) == 0.0


def test_governance_and_resolved_rows_never_count():
    findings, _ = _scored(_result("complex_method", Severity.HIGH, "a"))
    resolved, _ = _scored(_result("complex_method", Severity.CRITICAL, "b", ccn=90))
    resolved[0].status = "resolved"
    governance, _ = _scored(_result("ungoverned_hotspot", Severity.HIGH))
    rows = findings + resolved + governance
    assert removal_gain(rows, [(findings[0], 1.0)]) == removal_gain(findings, [(findings[0], 1.0)])


def test_structure_freed_ignores_history():
    findings, _ = _scored(_result("complex_method", Severity.MEDIUM, "a"))
    assert structure_freed(findings, [(findings[0], 1.0)]) == pytest.approx(
        findings[0].health_impact, abs=1e-3
    )


def _plan(target: str, share: float | None, impact: float) -> RefactoringSuggestion:
    evidence = {} if share is None else {"recovered_share": share}
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path="m.py",
        target_symbol=target,
        line_start=1,
        line_end=2,
        plan={},
        evidence=evidence,
        impact_delta=impact,
        effort_bucket="S",
        blast_radius={},
        confidence="high",
        source_biomarker="complex_method",
    )


def test_opportunity_prices_its_steps_together():
    findings, _ = _scored(
        *(_result("complex_method", Severity.CRITICAL, f"f{i}", ccn=60) for i in range(8))
    )
    rows = [(_plan(f"f{i}", 1.0, removal_gain(findings, [(findings[i], 1.0)])), f"p{i}") for i in range(8)]
    credited = {plan_id for _, plan_id in rows}
    each = sum(s.impact_delta for s, _ in rows)
    together = _recoverable(rows, credited, findings)
    assert together == removal_gain(findings, [(f, 1.0) for f in findings])
    assert together > 10 * each


def test_opportunity_without_shares_sums_its_steps():
    findings, _ = _scored(_result("complex_method", Severity.HIGH, "a"))
    rows = [(_plan("a", None, 0.4), "p0")]
    assert _recoverable(rows, {"p0"}, findings) == 0.4
    assert _recoverable(rows, {"p0"}, None) == 0.4


def test_opportunity_prices_only_credited_steps_with_a_share():
    findings, _ = _scored(
        *(_result("complex_method", Severity.CRITICAL, f"f{i}", ccn=60) for i in range(3))
    )
    rows = [(_plan("f0", 1.0, 0.3), "p0"), (_plan("f1", 0.0, 0.3), "p1"), (_plan("f2", 1.0, 0.3), "p2")]
    only_f0 = removal_gain(findings, [(findings[0], 1.0)])
    # p2 is not credited and p1 frees nothing.
    assert _recoverable(rows, {"p0", "p1"}, findings) == only_f0 > 0
    # No findings to rescore: the credited steps' own gains are summed.
    assert _recoverable(rows, {"p0", "p1"}, None) == pytest.approx(0.6)


def test_one_step_without_a_share_sums_the_whole_opportunity():
    findings, _ = _scored(
        *(_result("complex_method", Severity.CRITICAL, f"f{i}", ccn=60) for i in range(2))
    )
    rows = [(_plan("f0", 1.0, 0.3), "p0"), (_plan("f1", None, 0.5), "p1")]
    assert _recoverable(rows, {"p0", "p1"}, findings) == pytest.approx(0.8)
