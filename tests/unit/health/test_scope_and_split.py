"""What health scores, and the two halves of the number it produces."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import BiomarkerResult, FileContext
from repowise.core.analysis.health.biomarkers.hidden_coupling import HiddenCouplingDetector
from repowise.core.analysis.health.biomarkers.untested_hotspot import UntestedHotspotDetector
from repowise.core.analysis.health.models import (
    HealthFileMetricData,
    Severity,
    split_by_origin,
)
from repowise.core.analysis.health.scope import parse_scope, scores_language
from repowise.core.analysis.health.scoring import (
    SCORE_MAX,
    attach_impacts,
    compute_kpis,
    deduction_split,
    headline_split,
    score_file,
    unclamped_score,
)


def _result(biomarker: str, severity: Severity = Severity.HIGH) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_type=biomarker,
        severity=severity,
        function_name=None,
        line_start=None,
        line_end=None,
        details={},
    )


# --------------------------------------------------------------------------- #
# Which files are scored at all
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("language", ["python", "typescript", "go", "shell", "elixir"])
def test_code_is_scored(language: str) -> None:
    assert scores_language(language)


@pytest.mark.parametrize(
    "language", ["markdown", "json", "yaml", "toml", "unknown", "html", None, ""]
)
def test_prose_and_configuration_are_not_scored(language: str | None) -> None:
    """A changelog every fix commit touches otherwise reads as a bug magnet."""
    assert not scores_language(language)


def test_sql_is_scored_despite_being_declared_non_code() -> None:
    """The registry calls it non-code because nothing parses it into symbols.

    Health does walk it, through sqlglot, and carries markers of its own for it,
    so excluding it on the registry flag alone would silently drop them.
    """
    assert scores_language("sql")


def test_unrecognised_scope_falls_back_to_the_default() -> None:
    """A bad value reads everything rather than silently reading nothing."""
    assert parse_scope("nonsense") == "all"
    assert parse_scope(None) == "all"


# --------------------------------------------------------------------------- #
# Test-file gating
# --------------------------------------------------------------------------- #


def test_a_test_file_is_never_an_untested_hotspot() -> None:
    """With coverage ingested the suite reads as 0% covered and would fire."""
    ctx = FileContext(
        file_path="tests/unit/test_auth.py",
        language="python",
        nloc=400,
        has_test_file=False,
        module="tests",
        git_meta={"is_hotspot": True, "commit_count_90d": 40},
        dependents_count=12,
        line_coverage_pct=0.0,
    )
    assert UntestedHotspotDetector().detect(ctx) == []


def test_two_tests_moving_together_is_not_hidden_coupling() -> None:
    partners = (
        '[{"file_path": "tests/unit/test_db.py", "support": 20, "self_commits": 22, '
        '"partner_commits": 24, "structural": "unexplained"}]'
    )
    ctx = FileContext(
        file_path="tests/unit/test_auth.py",
        language="python",
        nloc=400,
        has_test_file=True,
        module="tests",
        git_meta={"co_change_partners_json": partners},
    )
    assert HiddenCouplingDetector().detect(ctx) == []


# --------------------------------------------------------------------------- #
# The structure / history split
# --------------------------------------------------------------------------- #


def test_the_two_halves_sum_to_the_whole_deduction() -> None:
    results = [
        _result("god_class", Severity.CRITICAL),
        _result("complex_method"),
        _result("change_entropy"),
        _result("co_change_scatter", Severity.CRITICAL),
    ]
    scores, deductions = score_file(results)
    findings = attach_impacts(results, deductions)
    structure, history = deduction_split(findings)
    assert structure > 0 and history > 0
    assert round(structure + history, 3) == round(sum(deductions), 3)
    assert round(SCORE_MAX - structure - history, 2) == round(scores["defect"], 2)


def test_history_alone_can_hold_a_score_down() -> None:
    results = [_result("co_change_scatter", Severity.CRITICAL) for _ in range(5)]
    _, deductions = score_file(results)
    structure, history = deduction_split(attach_impacts(results, deductions))
    assert structure == 0.0
    assert history > 0


def _row(path: str, score: float, structure: float, history: float, nloc: int = 100):
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=1,
        max_nesting=1,
        nloc=nloc,
        has_test_file=False,
        structure_deduction=structure,
        history_deduction=history,
    )


def test_a_floored_file_splits_only_the_points_it_lost() -> None:
    """Unclamped 12.71 = 9.21 + 3.5, floored at 1.0: the halves share 9.0."""
    structure, history = headline_split([_row("a.py", 1.0, 9.21, 3.5)])
    assert structure is not None and history is not None
    assert round(structure + history, 6) == SCORE_MAX - 1.0
    assert round(structure / history, 6) == round(9.21 / 3.5, 6)


def test_the_headline_halves_add_up_to_its_deficit() -> None:
    rows = [
        _row("floored.py", 1.0, 9.21, 3.5, nloc=300),
        _row("fine.py", 8.5, 1.0, 0.5, nloc=200),
        _row("clean.py", 10.0, 0.0, 0.0, nloc=50),
    ]
    kpis = compute_kpis(rows, {"floored.py"})
    for scope, headline in (("average", "average_health"), ("hotspot", "hotspot_health")):
        halves = kpis[f"structure_{scope}"] + kpis[f"history_{scope}"]
        assert abs(halves - (SCORE_MAX - kpis[headline])) <= 0.011


def test_the_split_is_unknown_before_files_carry_it() -> None:
    legacy = HealthFileMetricData(
        file_path="a.py", score=5.0, max_ccn=1, max_nesting=1, nloc=10, has_test_file=False
    )
    assert headline_split([legacy]) == (None, None)
    assert headline_split([legacy, _row("b.py", 8.0, 1.5, 0.5)]) == (None, None)


def test_a_floored_file_still_shows_its_depth() -> None:
    """Two files pinned at 1.0 are not equally deep, and the score cannot say so."""
    shallow = unclamped_score(6.0, 4.0)
    deep = unclamped_score(6.0, 12.0)
    assert shallow == 0.0
    assert deep == -8.0
    assert unclamped_score(None, None) is None


def test_split_by_origin_separates_what_an_edit_can_fix() -> None:
    code_shape, history = split_by_origin(
        [_result("complex_method"), _result("prior_defect"), _result("god_class")]
    )
    assert [f.biomarker_type for f in code_shape] == ["complex_method", "god_class"]
    assert [f.biomarker_type for f in history] == ["prior_defect"]
