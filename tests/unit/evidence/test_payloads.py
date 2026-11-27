"""The runtime evidence route payloads, from plain rows (no store)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.evidence.payloads import (
    function_payload,
    functions_payload,
    measured_payload,
    runs_payload,
    status_payload,
    weighted_callers,
)

VIEW = {"measured": {"runs": [], "rows": []}, "measured_in_tests": None}


def test_measured_with_a_view_is_measured() -> None:
    assert measured_payload(VIEW, "measured") == {
        "basis": "measured",
        "status": "measured",
        "command": None,
        "measured": VIEW["measured"],
        "measured_in_tests": None,
        "statements": None,
        "benchmarks": None,
    }


def test_measured_without_a_view_names_why_and_the_next_command() -> None:
    # Something is ranked, just not in the asked run.
    assert measured_payload(None, "measured")["status"] == "not_ranked"
    assert measured_payload(None, "measured")["command"] is None
    empty = measured_payload(None, "no_runtime_data")
    assert (empty["basis"], empty["status"]) == ("unknown", "no_runtime_data")
    assert empty["command"].startswith("repowise evidence setup")
    assert measured_payload(None, "needs_update")["command"] == "repowise update"


def test_status_and_runs_with_no_runs() -> None:
    assert status_payload([], "no_runtime_data", "abc")["reference_commit"] == "abc"
    assert status_payload([], "no_runtime_data", "abc")["workloads"] == []
    assert runs_payload([], "abc") == {"reference_commit": "abc", "runs": []}


def _hot(sid: str, tier: str) -> SimpleNamespace:
    fields = ["rank", "self_share", "total_share", "share_low", "share_high", "samples", "run_samples", "run_id", "run_count", "workload_name", "workload_class", "value_kind", "ordering_only", "freshness", "evidence_commit_sha", "measured_at", "repo_self_share", "wait_share", "library_share", "run_unlisted"]
    return SimpleNamespace(symbol_id=sid, tier=tier, **dict.fromkeys(fields))


def test_functions_sorted_by_id_and_tiers_above_ranked_read_ranked() -> None:
    rows = {"b.py::g": _hot("b.py::g", "measured"), "a.py::f": _hot("a.py::f", "lead_candidate")}
    out = functions_payload(rows)["functions"]
    assert [(f["symbol_id"], f["file_path"], f["tier"]) for f in out] == [
        ("a.py::f", "a.py", "ranked"),
        ("b.py::g", "b.py", "measured"),
    ]
    assert function_payload(_hot("c.py::h", "ranked"))["tier"] == "ranked"


def test_inside_and_drivers_read_from_their_json_and_unreadable_reads_as_none() -> None:
    from repowise.server.schemas.runtime_evidence import MeasuredFunction

    row = _hot("a.py::f", "ranked")
    row.inside_json = '[{"name": "open", "kind": "library", "share": 0.6, "leaf": {"name": "stat", "kind": "native", "share": 0.5}}]'
    row.drivers_json = '[{"name": "main", "share": 1.0, "symbol": "a.py::main"}]'
    out = function_payload(row)
    assert out["inside"][0]["leaf"]["name"] == "stat"
    assert out["drivers"] == [{"name": "main", "share": 1.0, "symbol": "a.py::main"}]
    MeasuredFunction.model_validate(out)
    row.inside_json, row.drivers_json = "not json", "[]"
    assert (function_payload(row)["inside"], function_payload(row)["drivers"]) == (None, None)
    # A host row from before the columns existed has neither.
    assert function_payload(_hot("b.py::g", "ranked"))["inside"] is None


def test_next_step_from_the_row_and_the_file_language() -> None:
    from repowise.server.schemas.runtime_evidence import MeasuredFunction

    row = _hot("lib/view.js::tryStat", "ranked")
    row.workload_name, row.workload_class = "api load", "load_test"
    row.inside_json = '[{"name": "statSync", "kind": "library", "share": 0.99, "leaf": {"name": "stat", "kind": "native", "share": 0.76}}]'
    out = function_payload(row)
    assert out["next_step"]["rule"] == "filesystem"
    assert out["next_step"]["leaf"] == "stat"
    MeasuredFunction.model_validate(out)
    # A unit-test run, or a row with no callees, says nothing.
    row.workload_class = "unit_test"
    assert function_payload(row)["next_step"] is None
    assert function_payload(_hot("a.py::f", "ranked"))["next_step"] is None


def test_weighted_callers_lead_with_the_measured_caller_heaviest_first() -> None:
    drivers = json.dumps(
        [
            {"name": "b", "share": 0.2, "symbol": "b.py::b"},
            {"name": "c", "share": 0.7, "symbol": "c.py::c"},
            # Measured, but the graph does not list it: never added.
            {"name": "x", "share": 0.1, "symbol": "x.py::x"},
            # Never joined to a symbol: no weight on any caller.
            {"name": "a", "share": 0.9, "path": "a.py"},
        ]
    )
    callers = ["a.py::a", "b.py::b", "c.py::c", "d.py::d"]
    assert weighted_callers(callers, drivers, 3) == (
        ["c.py::c", "b.py::b", "a.py::a"],
        {"c.py::c": 0.7, "b.py::b": 0.2},
    )


def test_weighted_callers_without_drivers_keep_the_graph_order() -> None:
    callers = ["a.py::a", "b.py::b"]
    for drivers in (None, "", "not json", json.dumps({"symbol": "a.py::a"})):
        assert weighted_callers(callers, drivers, 3) == (callers, {})
