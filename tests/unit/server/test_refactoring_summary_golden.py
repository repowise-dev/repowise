"""Characterization of the refactoring board's summary chips.

Pins every count, the by-type order, both threshold edges and the rounding of
the best gain that the board and ``/refactoring/plans`` report.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.routers import refactoring


def _rec(kind: str, path: str, effort: str, impact: float) -> SimpleNamespace:
    return SimpleNamespace(
        suggestion=SimpleNamespace(
            refactoring_type=kind, file_path=path, effort_bucket=effort, impact_delta=impact
        )
    )


_RECS = [
    _rec("extract_method", "a.py", "S", 0.0999),
    _rec("extract_method", "a.py", "M", 0.1),
    _rec("split_file", "b.py", "L", 0.4999),
    _rec("break_cycle", "c.py", "S", 0.5),
    _rec("performance_fix", "c.py", "XL", 1.23456),
    _rec("move_method", "d.py", "", 0.0),
    _rec("extract_class", "e.py", "S", 2.34567),
    _rec("extract_helper", "e.py", "M", 0.25),
]


def test_summary_counts_and_threshold_edges() -> None:
    assert refactoring._summary(_RECS).model_dump() == {
        "total": 8,
        "by_type": [
            {"type": "extract_method", "count": 2},
            {"type": "break_cycle", "count": 1},
            {"type": "extract_class", "count": 1},
            {"type": "extract_helper", "count": 1},
            {"type": "move_method", "count": 1},
            {"type": "performance_fix", "count": 1},
            {"type": "split_file", "count": 1},
        ],
        "files_total": 5,
        "structural_total": 4,
        "performance_total": 1,
        "small_effort_total": 3,
        "health_recovery_total": 6,
        "negligible_health_total": 5,
        "best_health_gain": 2.346,
    }


def test_summary_of_nothing() -> None:
    assert refactoring._summary([]).model_dump() == {
        "total": 0,
        "by_type": [],
        "files_total": 0,
        "structural_total": 0,
        "performance_total": 0,
        "small_effort_total": 0,
        "health_recovery_total": 0,
        "negligible_health_total": 0,
        "best_health_gain": 0.0,
    }


def test_structural_lens() -> None:
    assert set(refactoring._STRUCTURAL_TYPES) == {
        "split_file",
        "break_cycle",
        "extract_class",
        "move_method",
    }
