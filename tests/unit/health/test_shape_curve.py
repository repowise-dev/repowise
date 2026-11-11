"""The defect score's code-shape aggregation: continuous critical severity,
per-function grouping and the saturating category curve."""

from __future__ import annotations

import math

import pytest

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import CATEGORY_CAPS, score_file
from repowise.core.analysis.health.shape_curve import CATEGORY_SATURATION


def _r(name: str, severity: Severity, fn: str | None = None, **details) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=severity,
        function_name=fn,
        line_start=1,
        line_end=2,
        details=details,
    )


def _defect(results: list[BiomarkerResult]) -> float:
    return score_file(results)[0]["defect"]


def test_halving_a_critical_function_raises_the_score():
    """CCN 50 -> 26 stays critical and still pays."""
    big = _defect([_r("complex_method", Severity.CRITICAL, "f", ccn=50)])
    half = _defect([_r("complex_method", Severity.CRITICAL, "f", ccn=26)])
    assert half > big


def test_a_second_critical_function_still_costs():
    one = _defect([_r("complex_method", Severity.CRITICAL, "f", ccn=30)])
    two = _defect(
        [
            _r("complex_method", Severity.CRITICAL, "f", ccn=30),
            _r("complex_method", Severity.CRITICAL, "g", ccn=30),
        ]
    )
    assert two < one


def test_one_function_is_charged_once_per_group_not_per_marker():
    """complex + large on one function deduct max + 0.25 x the other."""
    same = _defect(
        [_r("complex_method", Severity.MEDIUM, "f"), _r("large_method", Severity.MEDIUM, "f")]
    )
    apart = _defect(
        [_r("complex_method", Severity.MEDIUM, "f"), _r("large_method", Severity.MEDIUM, "g")]
    )
    assert same > apart
    raw = 0.7 * 1.25 + 0.25 * 0.7 * 1.21
    ceiling = CATEGORY_SATURATION * CATEGORY_CAPS["size_and_complexity"]
    assert same == pytest.approx(10.0 - ceiling * (1 - math.exp(-raw / ceiling)))


def test_continuous_critical_base_matches_the_curve():
    """CCN 50 is twice the critical threshold: base 4.0, weight 1.21."""
    ceiling = CATEGORY_SATURATION * CATEGORY_CAPS["size_and_complexity"]
    scores, ded = score_file([_r("complex_method", Severity.CRITICAL, "f", ccn=50)])
    expected = ceiling * (1 - math.exp(-4.0 * 1.21 / ceiling))
    assert scores["defect"] == pytest.approx(10.0 - expected)
    assert ded == [pytest.approx(expected)]


def test_history_cap_follows_the_saturated_structure():
    shape = [_r("complex_method", Severity.MEDIUM, "f")]
    history = [_r("prior_defect", Severity.CRITICAL) for _ in range(6)]
    scores, ded = score_file([*shape, *history])
    structure = ded[0]
    assert sum(ded[1:]) == pytest.approx(1.0 + structure)
    assert scores["defect"] == pytest.approx(10.0 - structure - (1.0 + structure))


def test_maintainability_keeps_its_hard_caps():
    findings = [_r("low_cohesion", Severity.CRITICAL, f"f{i}") for i in range(10)]
    assert score_file(findings)[0]["maintainability"] == 6.0


def test_breakdown_reports_function_grouped_raw():
    """The breakdown's raw figures follow the same per-function grouping."""
    from repowise.core.analysis.health.aggregation import score_breakdown
    from repowise.core.analysis.health.scoring import attach_impacts

    results = [_r("complex_method", Severity.MEDIUM, "f"), _r("large_method", Severity.MEDIUM, "f")]
    findings = attach_impacts(results, score_file(results)[1])
    size = score_breakdown(findings)["categories"][0]
    assert size["raw_deduction"] == round(0.7 * 1.25 + 0.25 * 0.7 * 1.21, 3)
    assert size["capped"] is False


def test_a_critical_function_growing_is_worsened():
    """A critical finding's raw deduction tracks its measure past the threshold."""
    from repowise.core.analysis.health.aggregation import finding_raw_deduction
    from repowise.core.analysis.health.scoring import attach_impacts

    def raw(ccn: int) -> float:
        results = [_r("complex_method", Severity.CRITICAL, "f", ccn=ccn)]
        return finding_raw_deduction(attach_impacts(results, score_file(results)[1])[0])

    assert raw(40) > raw(30) > raw(25)
