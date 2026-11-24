"""The evidence folds the performance queue reads, over plain rows (no store)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.evidence.measured_fold import (
    MeasuredFold,
    fold_measured,
    fold_query_readings,
    query_runs_to_read,
)
from repowise.core.evidence.query_counts import QueryReading


def _run(name: str, cls: str) -> SimpleNamespace:
    return SimpleNamespace(workload_name=name, workload_class=cls)


def _row(sid: str, rank: int, tier: str = "ranked", ordering_only: bool = False):
    return SimpleNamespace(symbol_id=sid, rank=rank, tier=tier, ordering_only=ordering_only)


def test_fold_measured_keeps_leading_and_test_runs_apart() -> None:
    fold = fold_measured(
        [
            (_run("api", "entry_point"), [_row("a", 1, "lead_candidate"), _row("b", 3)]),
            (_run("bench", "benchmark"), [_row("b", 2), _row("c", 1)]),
            (_run("tests", "unit_test"), [_row("a", 1), _row("t", 2, "lead_candidate")]),
            # Neither a leading class nor unit_test: never read.
            (_run("other", "integration_test"), [_row("x", 1, "lead_candidate")]),
            # A tracer's order is no measured cost.
            (_run("trace", "production"), [_row("z", 1, ordering_only=True)]),
        ]
    )
    assert fold == MeasuredFold(
        measured={"a": "api", "b": "bench", "c": "bench"},
        hot=frozenset({"a"}),
        workloads=("api", "bench"),
        in_tests={"a": "tests", "t": "tests"},
        hot_in_tests=frozenset({"t"}),
        ranks={"a": 1, "b": 2, "c": 1},
    )


def test_fold_measured_ties_break_by_workload_name() -> None:
    fold = fold_measured(
        [
            (_run("zeta", "production"), [_row("f", 1)]),
            (_run("alpha", "production"), [_row("f", 1)]),
        ]
    )
    assert fold.measured == {"f": "alpha"}
    assert fold_measured([]) == MeasuredFold({}, frozenset(), (), {}, frozenset(), {})


def _qrun(rid: str, name: str, day: int, partial: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        workload_name=name,
        environment="ci",
        measured_at=datetime(2026, 11, day, tzinfo=UTC),
        mapping_partial=partial,
    )


def test_fold_query_readings_reads_each_workloads_newest_complete_run() -> None:
    runs = [
        _qrun("old", "tests", 1),
        _qrun("new", "tests", 2),
        _qrun("partial", "api", 3, partial=True),
    ]
    assert query_runs_to_read(runs) == ["new"]
    points = [
        # Superseded run: ignored even though it reads "grows".
        ("old", "test", "s.py::f", "fp1", 1, 1),
        ("old", "test", "s.py::f", "fp1", 10, 10),
        # Newest: flat at f, grows at g.
        ("new", "test", "s.py::f", "fp1", 1, 2),
        ("new", "test", "s.py::f", "fp1", 10, 2),
        ("new", "test", "s.py::g", "fp2", 1, 1),
        ("new", "test", "s.py::g", "fp2", 5, 5),
        # A request scope is its own measurement: one size only, so it ran.
        ("new", "request", "s.py::g", "fp2", 3, 9),
        ("partial", "test", "s.py::h", "fp3", 1, 1),
    ]
    assert fold_query_readings(runs, points) == {
        "s.py::f": QueryReading("flat", 2, 10),
        "s.py::g": QueryReading("grows", 5, 5),
    }


def test_fold_query_readings_without_a_usable_run_is_empty() -> None:
    assert fold_query_readings([], [("r", "test", "s", "fp", 1, 1)]) == {}
    assert fold_query_readings([_qrun("p", "w", 1, partial=True)], []) == {}
