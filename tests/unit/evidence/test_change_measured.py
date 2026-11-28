"""``evidence.change_measured``: which changed functions a measurement speaks for."""

from __future__ import annotations

from repowise.core.evidence.change_measured import (
    MEASURED_SHOWN,
    Ranked,
    measured_block,
    measured_changes,
    measured_line,
)

BENCH = "go test -run '^$' -bench '^BenchmarkServe$' -count 5 ."


def test_only_changed_functions_in_a_leading_class_or_benchmarked_are_measured() -> None:
    found = measured_changes(
        ["a.go::serve", "a.go::load", "a.go::parse", "a.go::cold"],
        [
            ("a.go::serve", Ranked("api", "entry_point", 3)),
            ("a.go::load", Ranked("go test ./...", "unit_test", 1)),
            ("a.go::other", Ranked("api", "entry_point", 1)),  # not changed
        ],
        {"a.go::parse": BENCH, "a.go::unchanged": BENCH},
    )
    assert [(m.symbol_id, m.basis(), m.verify_basis) for m in found] == [
        ("a.go::serve", 'rank 3 in entry_point "api"', "workload"),
        ("a.go::parse", "benchmark", "benchmark"),
    ]


def test_the_best_rank_across_workloads_is_kept() -> None:
    [found] = measured_changes(
        ["a.go::serve"],
        [
            ("a.go::serve", Ranked("slow", "load_test", 7)),
            ("a.go::serve", Ranked("api", "entry_point", 2)),
        ],
        {},
    )
    assert found.ranked == Ranked("api", "entry_point", 2)


def test_a_function_with_no_command_is_left_out() -> None:
    # A production row with no workload name has no rerun to name.
    assert (
        measured_changes(["a.go::serve"], [("a.go::serve", Ranked(None, "production", 1))], {})
        == []
    )


def test_the_block_is_absent_when_nothing_measured_changed() -> None:
    assert measured_block([]) is None


def test_the_block_caps_its_rows_and_counts_the_rest() -> None:
    ids = [f"a.go::f{i}" for i in range(MEASURED_SHOWN + 2)]
    block = measured_block(measured_changes(ids, [], dict.fromkeys(ids, BENCH)))
    assert len(block["functions"]) == MEASURED_SHOWN
    assert block["total"] == MEASURED_SHOWN + 2
    assert block["summary"].startswith("This change edits 7 measured functions")
    assert measured_line(block["functions"][0]) == f"a.go::f0 (benchmark): {BENCH}"


def test_an_unnamed_better_rank_never_hides_a_named_rerun() -> None:
    [found] = measured_changes(
        ["a.go::serve"],
        [
            ("a.go::serve", Ranked(None, "production", 1)),
            ("a.go::serve", Ranked("api", "entry_point", 4)),
        ],
        {},
    )
    assert found.ranked == Ranked("api", "entry_point", 4)
    assert found.verify.startswith("repowise perf measure")
