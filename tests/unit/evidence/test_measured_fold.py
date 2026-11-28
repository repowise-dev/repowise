"""The evidence folds the performance queue reads, over plain rows (no store)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.evidence.measured_fold import (
    MeasuredFold,
    StatementCost,
    fold_measured,
    fold_query_readings,
    fold_statements,
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
            # A benchmark run never leads, even from rows stored before it stopped ranking.
            (_run("bench", "benchmark"), [_row("b", 2, "lead_candidate"), _row("c", 1)]),
            (_run("tests", "unit_test"), [_row("a", 1), _row("t", 2, "lead_candidate")]),
            # Neither a leading class nor unit_test: never read.
            (_run("other", "integration_test"), [_row("x", 1, "lead_candidate")]),
            # A tracer's order is no measured cost.
            (_run("trace", "production"), [_row("z", 1, ordering_only=True)]),
        ]
    )
    assert fold == MeasuredFold(
        measured={"a": "api", "b": "api"},
        hot=frozenset({"a"}),
        workloads=("api",),
        in_tests={"a": "tests", "t": "tests"},
        hot_in_tests=frozenset({"t"}),
        ranks={"a": 1, "b": 3},
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


def _window(run_id: str, name: str, day: int, partial: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id,
        workload_name=name,
        environment="prod",
        measured_at=datetime(2026, 11, day, tzinfo=UTC),
        mapping_partial=partial,
    )


def test_fold_statements_puts_only_exactly_joined_statements_on_a_function() -> None:
    runs = [
        _window("old", "orders-db", 1),
        _window("new", "orders-db", 2),
        _window("other", "billing-db", 3),
    ]
    points = [
        # Two statements one function issues, summed.
        ("new", "src/a.py::save", "function", 40, 600.0),
        ("new", "src/a.py::save", "function", 10, 200.0),
        ("new", "src/b.py::load", "function", 5, 100.0),
        # Never put on a function: several could issue it, only a schedule runs
        # it, or nothing matched.
        ("new", None, "ambiguous", 3, 50.0),
        ("new", None, "database_schedule", 1, 40.0),
        ("new", None, None, 2, 10.0),
        # A superseded window of the same workload never adds to the newest.
        ("old", "src/b.py::load", "function", 999, 9999.0),
        # A run that names no function still folds, so it reads joined-nothing.
        ("other", None, "ambiguous", 1, None),
    ]
    other, new = fold_statements(runs, points)
    assert (other.run.id, other.rows, other.statements) == ("other", [], 1)
    assert other.unattributed == {"ambiguous": {"statements": 1, "total_ms": 0.0, "share": None}}
    assert new.run.id == "new" and (new.total_ms, new.statements) == (1000.0, 6)
    assert new.rows == [
        StatementCost("src/a.py::save", "new", 1, 0.8, 800.0, 50, 2),
        StatementCost("src/b.py::load", "new", 2, 0.1, 100.0, 5, 1),
    ]
    assert new.unattributed == {
        "ambiguous": {"statements": 1, "total_ms": 50.0, "share": 0.05},
        "database_schedule": {"statements": 1, "total_ms": 40.0, "share": 0.04},
        "unjoined": {"statements": 1, "total_ms": 10.0, "share": 0.01},
    }


def test_fold_statements_without_a_complete_run_is_empty() -> None:
    assert fold_statements([], []) == []
    assert (
        fold_statements([_window("p", "db", 1, partial=True)], [("p", "x", "function", 1, 1.0)])
        == []
    )
