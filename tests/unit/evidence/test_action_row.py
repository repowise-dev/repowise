"""``evidence.action_row``: one measured function as one row, with one verify command."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS  # noqa: N814

from repowise.core.evidence.action_row import (
    action_row,
    row_compact,
    row_slower,
    row_step,
    row_text,
    row_verify,
    run_verify,
)
from repowise.core.evidence.compare import Cause, ChangedCommit, Comparison, Slower
from repowise.core.evidence.function_tests import RowTests

FN = "packages/cli/status_cmd.py::_index_storage_bytes"
DRIVER = "packages/cli/status_cmd.py::_workspace_rows"
BENCH = "pytest benches/test_status.py::test_storage --benchmark-only"
#: A run's ``inside`` for ``FN``: stat calls per entry of a directory walk.
INSIDE = [
    {"name": "is_file", "kind": "library", "share": 0.569,
     "leaf": {"name": "stat", "kind": "native", "share": 0.569}},
    {"name": "select_recursive", "kind": "library", "share": 0.249},
    {"name": "stat", "kind": "library", "share": 0.146},
]
DRIVERS = [{"name": "_workspace_rows", "share": 1.0, "symbol": DRIVER}]


def _measured(**over) -> NS:
    fields = {
        "symbol_id": FN, "run_id": "r1", "rank": 1, "self_share": 0.09, "share_low": 0.07,
        "share_high": 0.11, "total_share": 0.12, "samples": 63, "freshness": "current",
    }
    return NS(**{**fields, **over})


def _row(**over):
    kw = {
        "lines": (54, 63),
        "workload_name": "repowise status",
        "workload_class": "entry_point",
        "next_step": row_step(FN, INSIDE, DRIVERS, "repowise status", "entry_point"),
        "callers": ["packages/cli/status_cmd.py::status_command", DRIVER],
        "callers_total": 2,
        "drivers_json": json.dumps(DRIVERS),
        "tests": RowTests(["tests/unit/cli/test_status_storage.py::test_sums"], "per_test_map"),
        "history": {
            "owner": {"name": "Ada", "line_pct": 0.8},
            "last_change": {"sha": "abcdef1234", "date": "2026-10-03", "subject": "x", "pr": 42},
        },
    }
    return action_row(_measured(), **{**kw, **over})


def test_a_benchmark_measuring_the_function_beats_the_workload_rerun() -> None:
    assert row_verify(BENCH, "repowise status", "entry_point") == (BENCH, "benchmark")
    command, basis = row_verify(None, "repowise status", "entry_point")
    assert basis == "workload"
    assert command.startswith("repowise perf measure --save before -- repowise status")
    assert row_verify(None, "api", "load_test")[1] == "workload"
    # A unit-test run has no rerun that times one function: no command.
    assert row_verify(None, "pytest", "unit_test") == (None, None)
    assert run_verify({"workload_name": "pytest", "workload_class": "unit_test"}) is None


def test_the_json_form_holds_every_field_with_its_source() -> None:
    row = _row(benchmark=BENCH).as_dict()

    assert row["share"] == 0.09 and row["inclusive"] == 0.12 and row["lines"] == [54, 63]
    # The run measured the driver: it leads, with the share it drove.
    assert row["callers"] == [DRIVER, "packages/cli/status_cmd.py::status_command"]
    assert row["caller_shares"] == {DRIVER: 1.0}
    assert row["next_step"]["rule"] == "filesystem" and "os.scandir" in row["next_step"]["next"]
    # One verify command on the row; the step carries none of its own.
    assert "verify" not in row["next_step"]
    assert (row["verify"], row["verify_basis"]) == (BENCH, "benchmark")
    assert row["sources"] == {
        "share": "workload_hotness",
        "lines": "symbols",
        "callers": "call_graph",
        "caller_shares": "run_drivers",
        "owner": "function_blame",
        "last_change": "function_blame",
        "tests": "per_test_map",
        "next_step": "run_inside",
        "verify": "benchmark",
    }
    assert "slower" not in row and "on_static_findings" not in row


def test_silence_without_a_source() -> None:
    row = action_row(_measured(), workload_name="pytest", workload_class="unit_test").as_dict()

    # No blame row: no owner, no last change, never a file's; no step, no verify.
    assert (row["owner"], row["last_change"], row["next_step"]) == (None, None, None)
    assert (row["tests"], row["tests_basis"], row["verify"], row["verify_basis"]) == (
        None, None, None, None,
    )
    assert row["callers_total"] is None and row["callers"] == []
    assert row["sources"] == {"share": "workload_hotness"}


def _comparison(verdict: str = "slower", **over) -> Comparison:
    fields = {
        "kind": "profile", "workload": "repowise status", "environment": "local",
        "verdict": verdict, "band_runs": 4, "head_run_id": "r9", "current_runs": 1,
        "base_commit": "a" * 40, "head_commit": "b" * 40,
    }
    return Comparison(**{**fields, **over})


def test_a_slower_comparison_names_the_function_and_the_commit_that_changed_it() -> None:
    cause = Cause("a" * 40, "b" * 40, 3, (ChangedCommit("c" * 40, "speed", 12, (FN,)),))
    own = Slower("self_seconds", FN, 0.2, 0.28, 1.4, "s", symbol_id=FN)
    found = _comparison(slower=(own,), cause=cause)

    assert row_slower(FN, [_comparison("within_noise"), found]) == {
        "workload": "repowise status",
        "base_commit": "a" * 40,
        "metric": "self_seconds",
        "before": 0.2,
        "after": 0.28,
        "ratio": 1.4,
        "unit": "s",
        "changed_in": [{"sha": "c" * 40, "pr": 12}],
    }
    # Only a commit in the range changed it: named, with no ratio of its own.
    only_commit = row_slower(FN, [_comparison(slower=(), cause=cause)])
    assert only_commit["changed_in"] == [{"sha": "c" * 40, "pr": 12}] and "ratio" not in only_commit
    # A comparison that names other functions says nothing about this one.
    assert row_slower(FN, [_comparison(slower=(Slower("self_seconds", "x.py::y", 1, 2, 2),))]) is None

    row = _row(compare=[found]).as_dict()
    assert row["sources"]["slower"] == "compare"
    assert 'slower: 1.40x self_seconds in "repowise status" (0.2 to 0.28 s); changed in ccccccc (#12)' in (
        row_text(row)
    )


def test_the_text_form_leads_with_the_step_and_ends_with_verify() -> None:
    lines = row_text(_row().as_dict())

    assert lines[0].startswith("next: walk with os.scandir")
    assert lines[1].startswith("why: ") and "directory walk" in lines[1]
    assert lines[2] == "driven by: status_cmd.py::_workspace_rows 100%"
    assert lines[3] == (
        "self 9% of the run (7% to 11%) · with callees 12% · 63 samples · 2 callers · "
        "owner Ada · last changed 2026-10-03 (abcdef1, #42) · "
        "test that ran it tests/unit/cli/test_status_storage.py::test_sums"
    )
    assert lines[4].startswith("verify: repowise perf measure --save before -- repowise status")
    assert len(lines) == 5


def test_the_mcp_form_is_the_action_with_no_bookkeeping() -> None:
    row = _row().as_dict()
    compact = row_compact(row)

    assert set(compact) == {"symbol_id", "run_id", "share", "next"}
    assert compact["next"] == row["next_step"]["next"]
    # A benchmark's command is the row's own; a workload rerun sits on the run.
    assert row_compact(_row(benchmark=BENCH).as_dict())["verify"] == BENCH
    silent = row_compact(
        action_row(_measured(), static_causes={"perf3_b", "perf3_a"}).as_dict()
    )
    assert silent == {
        "symbol_id": FN, "run_id": "r1", "share": 0.09, "on_static_findings": ["perf3_a", "perf3_b"],
    }
