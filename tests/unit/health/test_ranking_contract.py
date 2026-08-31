"""The worst-first comparator, over every record shape that reaches it.

A health row arrives as an analyzer dataclass before persistence, as an ORM row
after it, and as a plain dict in tests, fixtures and any consumer reading a
serialized report. The ranking is a wire-visible ordering, so all three must
produce the same list — otherwise "the worst files" depends on which surface
asked.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.ranking import (
    deduction_by_path,
    sort_metrics_worst_first,
    worst_first_key,
    worst_metric,
)
from repowise.core.analysis.health.scoring import SCORE_FLOOR


@dataclass
class _MetricData:
    """Stands in for the analyzer's ``HealthFileMetricData``."""

    file_path: str
    score: float
    nloc: int = 10


class _Row:
    """Stands in for an ORM row: plain attributes, not a dataclass."""

    def __init__(self, file_path: str, score: float, nloc: int = 10) -> None:
        self.file_path = file_path
        self.score = score
        self.nloc = nloc


def _dict(file_path: str, score: float, nloc: int = 10) -> dict:
    return {"file_path": file_path, "score": score, "nloc": nloc}


def _shapes(rows: list[tuple[str, float]]) -> list[list]:
    """The same rows as dicts, dataclasses and attribute rows."""
    return [
        [_dict(path, score) for path, score in rows],
        [_MetricData(path, score) for path, score in rows],
        [_Row(path, score) for path, score in rows],
    ]


def _paths(ordered: list) -> list[str]:
    return [
        row["file_path"] if isinstance(row, dict) else row.file_path for row in ordered
    ]


# --------------------------------------------------------------------------- #
# Record-shape equivalence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rows,deductions,expected",
    [
        # Score decides first.
        ([("a.py", 8.0), ("b.py", 2.0), ("c.py", 5.0)], {}, ["b.py", "c.py", "a.py"]),
        # Floor ties fall to deduction, deepest first.
        (
            [("a.py", SCORE_FLOOR), ("b.py", SCORE_FLOOR), ("c.py", SCORE_FLOOR)],
            {"a.py": 3.0, "b.py": 12.9, "c.py": 9.0},
            ["b.py", "c.py", "a.py"],
        ),
        # A file with no findings has no magnitude, so it sorts last in the band.
        (
            [("z.py", SCORE_FLOOR), ("a.py", SCORE_FLOOR)],
            {"z.py": 4.0},
            ["z.py", "a.py"],
        ),
        # Equal score and equal deduction: path breaks the tie, so paging is stable.
        (
            [("b.py", SCORE_FLOOR), ("a.py", SCORE_FLOOR)],
            {"a.py": 5.0, "b.py": 5.0},
            ["a.py", "b.py"],
        ),
        # Score still outranks depth: a 2.0 file precedes a deeper floored one.
        (
            [("deep.py", SCORE_FLOOR), ("mid.py", 2.0)],
            {"deep.py": 25.0},
            ["deep.py", "mid.py"],
        ),
    ],
)
def test_every_record_shape_ranks_identically(rows, deductions, expected) -> None:
    for shape in _shapes(rows):
        assert _paths(sort_metrics_worst_first(shape, deductions)) == expected


def test_the_key_itself_is_identical_across_shapes() -> None:
    deductions = {"a.py": 4.5}
    keys = {
        worst_first_key(row, deductions) for shape in _shapes([("a.py", 1.0)]) for row in shape
    }
    assert keys == {(1.0, -4.5, "a.py")}


def test_sorting_leaves_its_input_alone() -> None:
    rows = [_dict("a.py", 1.0), _dict("b.py", 1.0)]
    assert _paths(sort_metrics_worst_first(rows, {"b.py": 5.0})) == ["b.py", "a.py"]
    assert _paths(rows) == ["a.py", "b.py"]


def test_an_unscored_row_is_not_treated_as_the_worst() -> None:
    """A missing measurement is not evidence of a problem."""
    rows = [{"file_path": "unmeasured.py"}, _dict("bad.py", 1.0)]
    assert _paths(sort_metrics_worst_first(rows, {})) == ["bad.py", "unmeasured.py"]


# --------------------------------------------------------------------------- #
# deduction_by_path
# --------------------------------------------------------------------------- #


def test_deductions_sum_every_finding_on_a_file_across_shapes() -> None:
    @dataclass
    class _Finding:
        file_path: str
        health_impact: float

    dicts = [
        {"file_path": "a.py", "health_impact": 1.25},
        {"file_path": "a.py", "health_impact": 2.5},
        {"file_path": "b.py", "health_impact": 0.5},
    ]
    objects = [_Finding(f["file_path"], f["health_impact"]) for f in dicts]
    expected = {"a.py": 3.75, "b.py": 0.5}
    assert deduction_by_path(dicts) == expected
    assert deduction_by_path(objects) == expected


def test_pathless_and_null_impact_findings_do_not_corrupt_the_map() -> None:
    rows = [
        {"file_path": "", "health_impact": 9.0},
        {"file_path": None, "health_impact": 9.0},
        {"file_path": "a.py", "health_impact": None},
    ]
    assert deduction_by_path(rows) == {"a.py": 0.0}


# --------------------------------------------------------------------------- #
# worst_metric
# --------------------------------------------------------------------------- #


def test_worst_metric_agrees_with_the_head_of_the_sorted_list() -> None:
    rows = [_dict("a.py", 1.0), _dict("b.py", 1.0), _dict("c.py", 4.0)]
    deductions = {"a.py": 2.0, "b.py": 7.0}
    assert worst_metric(rows, deductions) is sort_metrics_worst_first(rows, deductions)[0]


def test_worst_metric_is_none_for_no_rows() -> None:
    assert worst_metric([], {}) is None


def test_the_deduction_map_has_no_default() -> None:
    """Omitting it would silently tie the whole floor band on path."""
    with pytest.raises(TypeError):
        sort_metrics_worst_first([_dict("a.py", 1.0)])  # type: ignore[call-arg]
