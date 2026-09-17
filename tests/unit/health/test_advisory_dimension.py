"""The advisory dimension never moves a score, and cannot start to by accident.

``HealthFindingData.dimension`` defaults to ``"defect"`` and ``dimensions_for``
defaults to ``{"defect"}``, from two SEPARATE tables. A marker listed in only
one of them displays as advisory while deducting from the defect score, and
nothing caught that before these tests: the failure is silent, and it shows up
as a defect regression on every repo at once.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult
from repowise.core.analysis.health.biomarkers.registry import registered_biomarkers
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.scoring import (
    ADVISORY_DIMENSION,
    ALL_DIMENSIONS,
    CATEGORY_CAPS,
    DIMENSIONS,
    attach_impacts,
    biomarker_dimension,
    dimensions_for,
    is_advisory,
    score_file,
)

_REGISTERED = [b.name for b in registered_biomarkers()]


def _result(name: str, severity: Severity = Severity.HIGH) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=name,
        severity=severity,
        function_name="f",
        line_start=1,
        line_end=2,
        details={},
    )


# ---------------------------------------------------------------------------
# The dimension itself
# ---------------------------------------------------------------------------


def test_advisory_is_not_a_scored_dimension():
    """``DIMENSIONS`` is the set that carries a score, and stays three."""
    assert ADVISORY_DIMENSION not in DIMENSIONS
    assert (*DIMENSIONS, ADVISORY_DIMENSION) == ALL_DIMENSIONS


def test_score_file_returns_no_advisory_score():
    """A dimension with no number cannot have one drift."""
    scores, _ = score_file([_result("mock_saturated_test")])
    assert ADVISORY_DIMENSION not in scores
    assert set(scores) == set(DIMENSIONS)


def test_advisory_has_no_weight_category_or_cap_table():
    """Nothing is keyed on the advisory dimension in the scoring tables."""
    assert ADVISORY_DIMENSION not in CATEGORY_CAPS


@pytest.mark.parametrize("severity", list(Severity))
def test_advisory_findings_never_deduct_at_any_severity(severity):
    """Severity carries urgency for a reader; it must not reach a score."""
    scores, deductions = score_file([_result("mock_saturated_test", severity)])
    assert scores["defect"] == 10.0
    assert scores["maintainability"] == 10.0
    assert scores["performance"] == 10.0
    assert deductions == [0.0]


def test_advisory_findings_carry_zero_health_impact():
    results = [_result("mock_saturated_test")]
    _, deductions = score_file(results)
    findings = attach_impacts(results, deductions)
    assert findings[0].health_impact == 0.0
    assert findings[0].dimension == ADVISORY_DIMENSION


def test_advisory_findings_do_not_dilute_a_real_deduction():
    """Adding an advisory finding to a scored one leaves the score alone."""
    scored_only, _ = score_file([_result("brain_method")])
    with_advisory, _ = score_file([_result("brain_method"), _result("mock_saturated_test")])
    assert scored_only == with_advisory
    assert with_advisory["defect"] < 10.0


# ---------------------------------------------------------------------------
# The trap: home and scoring membership are two tables that must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _REGISTERED)
def test_registered_biomarker_homes_where_it_scores(name):
    """Every registered marker's home dimension is one it actually feeds.

    This is the guard the default trap needs. ``biomarker_dimension`` (display)
    and ``dimensions_for`` (scoring) are independent maps, so a marker added to
    ``_ADVISORY_HOME`` alone keeps the ``{"defect"}`` default and deducts from
    the surfaced score while claiming to be advisory - and a marker added to
    ``_BIOMARKER_DIMENSIONS`` alone scores nothing while displaying as a defect.
    """
    assert biomarker_dimension(name) in dimensions_for(name), (
        f"{name} homes to {biomarker_dimension(name)!r} but its deduction feeds "
        f"{sorted(dimensions_for(name))}"
    )


@pytest.mark.parametrize("name", _REGISTERED)
def test_advisory_markers_feed_no_scoring_dimension(name):
    """An advisory marker is in the advisory dimension and nothing else."""
    if not is_advisory(name):
        return
    assert dimensions_for(name) == {ADVISORY_DIMENSION}
    assert not dimensions_for(name) & set(DIMENSIONS)


@pytest.mark.parametrize("name", _REGISTERED)
def test_scoring_markers_are_not_flagged_advisory(name):
    """The predicate and the home label agree in both directions."""
    assert is_advisory(name) is (biomarker_dimension(name) == ADVISORY_DIMENSION)


def test_an_unregistered_biomarker_cannot_acquire_a_scoring_dimension_silently():
    """An unknown id defaults into ``defect`` - so it must never be advisory.

    The default is the whole trap: a marker someone forgot to register scores
    as a defect. That is the safe direction (it is visible, not silent), but it
    means "unknown" and "advisory" must never be the same answer, or a typo in
    ``_ADVISORY_HOME`` would read as a working advisory marker.
    """
    unknown = "not_a_registered_biomarker_at_all"
    assert dimensions_for(unknown) == {"defect"}
    assert biomarker_dimension(unknown) == "defect"
    assert not is_advisory(unknown)


def test_every_advisory_home_entry_is_actually_registered():
    """A name in ``_ADVISORY_HOME`` with no detector behind it is dead data."""
    from repowise.core.analysis.health.scoring import _ADVISORY_HOME

    assert set(_ADVISORY_HOME) <= set(_REGISTERED)


# ---------------------------------------------------------------------------
# Surfaces that must not read an advisory finding as a cost or a regression
# ---------------------------------------------------------------------------


def _finding(name: str, impact: float, dimension: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=name,
        severity=Severity.MEDIUM,
        file_path="tests/test_x.py",
        function_name="test_x",
        line_start=1,
        line_end=2,
        details={},
        health_impact=impact,
        dimension=dimension,
    )


def test_an_advisory_finding_is_never_a_files_dominant_cause():
    """``primary_finding`` names the one reason a file is unhealthy.

    A marker that costs the file nothing must not become the headline
    accusation on a file whose only open finding is advisory.
    """
    from repowise.core.analysis.health.models import primary_finding

    advisory = _finding("mock_saturated_test", 0.0, ADVISORY_DIMENSION)
    assert primary_finding([advisory]) is None
    scored = _finding("brain_method", 0.5, "defect")
    assert primary_finding([advisory, scored]) is scored


def test_score_breakdown_does_not_invent_a_deduction_for_an_advisory_finding():
    """The breakdown explains where a file's points went.

    Left in, an advisory finding contributes a raw deduction the file never
    paid and trips ``capped`` on a category that was never capped.
    """
    from repowise.core.analysis.health.aggregation import score_breakdown

    result = score_breakdown([_finding("mock_saturated_test", 0.0, ADVISORY_DIMENSION)])
    assert result["categories"] == []
    assert result["total_deduction"] == 0.0


def test_severity_breakdown_excludes_advisory():
    """It renders beside ``open_findings``, which also excludes advisory."""
    from repowise.core.analysis.health.aggregation import severity_breakdown

    counts = severity_breakdown(
        [
            _finding("mock_saturated_test", 0.0, ADVISORY_DIMENSION),
            _finding("brain_method", 0.5, "defect"),
        ]
    )
    assert counts["medium"] == 1


def test_zero_impact_dimensions_is_the_one_owner():
    """Three surfaces used to spell this set as their own literal."""
    from repowise.core.analysis.health.scoring import ZERO_IMPACT_DIMENSIONS

    assert ADVISORY_DIMENSION in ZERO_IMPACT_DIMENSIONS
    assert "performance" in ZERO_IMPACT_DIMENSIONS
    assert "defect" not in ZERO_IMPACT_DIMENSIONS


def test_advisory_is_absent_from_every_change_delta_counter():
    """Introduced, worsened, resolved AND unchanged.

    The counters are derived from one match, so advisory is dropped from both
    sides before matching rather than filtered per counter. ``unchanged_total``
    is the one that made this worth doing: an advisory finding present on both
    sides is not introduced, worsened or resolved, so every per-counter filter
    missed it while it silently inflated the total beside them.
    """
    from repowise.core.analysis.change_health.matcher import FindingMatcher

    advisory = _finding("mock_saturated_test", 0.0, ADVISORY_DIMENSION)
    scored = _finding("brain_method", 0.5, "defect")
    keep = [f for f in (advisory, scored) if not is_advisory(f.biomarker_type)]

    both_sides = FindingMatcher({}).match([advisory, scored], [advisory, scored])
    assert both_sides.unchanged_total == 2, "the matcher itself counts what it is given"

    filtered = FindingMatcher({}).match(keep, keep)
    assert filtered.unchanged_total == 1
    assert filtered.resolved == []
    assert filtered.of_kind("introduced", "worsened") == []
