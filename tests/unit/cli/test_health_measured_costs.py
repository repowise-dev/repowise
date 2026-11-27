"""``repowise health`` shows measured costs only with ranked evidence, always with their basis."""

from __future__ import annotations

import io
import json
import re
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.cli.commands.health_cmd.summary import _render_measured_costs
from tests.unit.cli.test_health_cmd_withheld import _run

RUN = {
    "run_id": "r1",
    "workload_class": "entry_point",
    "workload_name": "api-replay",
    "commit": "abcdef1234",
    "measured_at": "2026-11-15T00:00:00+00:00",
    "age_days": 3,
    "samples": 5300,
    "repo_self_share": 0.467,
    "wait_share": 0.3,
    "library_share": 0.116,
    "outside_production": 4,
    "value_kind": "wall",
    "ordering_only": False,
}
ROW = {
    "symbol_id": "src/app.py::serve",
    "file_path": "src/app.py",
    "lines": [1, 20],
    "run_id": "r1",
    "rank": 1,
    "share": 0.3,
    "share_low": 0.27,
    "share_high": 0.33,
    "inclusive": 0.94,
    "samples": 1590,
    "freshness": "current",
    "callers_total": 2,
    "callers": ["src/main.py::main"],
    "owner": {"name": "Ada", "line_pct": 0.8},
    "last_change": {"sha": "abcdef1234", "date": "2026-10-03", "subject": "x (#42)", "pr": 42},
    "tests": ["tests/test_app.py::test_serve"],
    "tests_basis": "call_graph",
    "verify": None,
}
VIEW = {
    "measured": {"runs": [RUN], "rows": [ROW]},
    "measured_in_tests": {
        "runs": [{**RUN, "run_id": "r3", "workload_class": "unit_test", "workload_name": "pytest"}],
        "rows": [{**ROW, "symbol_id": "src/app.py::thin", "run_id": "r3", "tests": None,
                  "on_static_findings": ["perf3_x"]}],
    },
}


def _rendered(view) -> str:
    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch("repowise.cli.commands.health_cmd.summary.console", console):
        _render_measured_costs(view)
    return console.file.getvalue()


def test_each_run_states_its_basis_and_where_its_time_went() -> None:
    out = _rendered(VIEW)
    assert (
        'Measured (entry_point "api-replay", at abcdef1, 3 days old: '
        "repo code itself 47%, waits 30%, libraries 12% of 5,300 samples)" in out
    )
    assert "Measured in tests (unit_test" in out
    assert "src/app.py::serve" in out and "src/app.py::thin" in out
    # The cost leads; inclusive time is context, never "94% of the run".
    assert (
        "self 30% of the run (27% to 33%) · with callees 94% · 1,590 samples · 2 callers · "
        "owner Ada · last changed 2026-10-03 (abcdef1, #42) · nearest test" in out
    )
    assert "94% of the run" not in out
    assert "nearest test by call graph tests/test_app.py::test_serve" in out
    assert "tests not mapped · static cause perf3_x" in out
    assert not re.search(r"\b(hot|cold)\b", out, re.IGNORECASE)


def test_a_row_names_the_test_that_ran_it_and_the_benchmark_that_verifies() -> None:
    row = {
        **ROW,
        "tests": ["tests/test_app.py::test_run"],
        "tests_basis": "per_test_map",
        "verify": "pytest benches/bench_app.py::test_serve --benchmark-only",
    }
    out = _rendered({"measured": {"runs": [RUN], "rows": [row]}})
    assert "test that ran it tests/test_app.py::test_run" in out
    assert "verify: pytest benches/bench_app.py::test_serve --benchmark-only" in out
    empty = _rendered({"measured": {"runs": [RUN], "rows": [{**row, "tests": []}]}})
    assert "no test ran it" in empty


def test_a_run_stored_before_the_split_says_so() -> None:
    run = {**RUN, "repo_self_share": None, "wait_share": None, "library_share": None}
    out = _rendered({"measured": {"runs": [run], "rows": [ROW]}})
    assert "time split unknown (stored before it was recorded; add the profile again)" in out


def test_no_view_prints_nothing() -> None:
    assert _rendered(None) == ""


def test_json_adds_the_view_only_with_evidence(monkeypatch) -> None:
    monkeypatch.setattr(health_module, "_load_measured_costs", lambda _path: None)
    stdout, _ = _run(monkeypatch, "--format", "json")
    assert "measured_costs" not in json.loads(stdout[stdout.index("{") :])

    monkeypatch.setattr(health_module, "_load_measured_costs", lambda _path: VIEW)
    stdout, _ = _run(monkeypatch, "--format", "json")
    payload = json.loads(stdout[stdout.index("{") :])
    assert payload["measured_costs"] == VIEW
    assert not re.search(r"\b(hot|cold)\b", json.dumps(payload["measured_costs"]))

    # Repository-wide, so a run narrowed to a file leaves it out.
    stdout, _ = _run(monkeypatch, "--format", "json", "--file", "src/a.py")
    assert "measured_costs" not in json.loads(stdout[stdout.index("{") :])


def test_the_connect_line_shows_only_without_runtime_evidence(monkeypatch) -> None:
    monkeypatch.setattr(health_module, "_load_measured_costs", lambda _path: None)
    absent = {"runtime": {"present": False}}
    monkeypatch.setattr(health_module, "_load_evidence_freshness", lambda _path: absent)
    stdout, _ = _run(monkeypatch)
    assert "No runtime evidence stored, so performance here is code shape only." in stdout
    assert "repowise evidence setup" in stdout
    assert not re.search(r"\b(hot|cold)\b", stdout, re.IGNORECASE)

    stored = {"runtime": {"present": True}}
    monkeypatch.setattr(health_module, "_load_evidence_freshness", lambda _path: stored)
    assert "No runtime evidence" not in _run(monkeypatch)[0]
    # A store that cannot say stays silent.
    monkeypatch.setattr(health_module, "_load_evidence_freshness", lambda _path: None)
    assert "No runtime evidence" not in _run(monkeypatch)[0]


def test_the_connect_line_comes_last(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(health_module, "_load_measured_costs", lambda _path: None)
    monkeypatch.setattr(health_module, "_load_evidence_freshness", lambda _path: {})
    for name in ("_render_files_table", "_render_findings_table", "_render_connect_line"):
        monkeypatch.setattr(health_module, name, lambda *_a, _n=name: calls.append(_n))
    _run(monkeypatch)
    assert calls == ["_render_files_table", "_render_findings_table", "_render_connect_line"]


def test_benchmarks_show_the_function_each_measures_and_numbers_per_op() -> None:
    bench_run = {"run_id": "b1", "workload_class": "benchmark", "workload_name": "go bench",
                 "commit": "abcdef1234", "measured_at": None, "age_days": 0}
    row = {"run_id": "b1", "benchmark": "BenchmarkGetMapFromFormData/Large_Bracket",
           "benchmark_symbol_id": "context_test.go::BenchmarkGetMapFromFormData",
           "measures": "context.go::getMapFromFormData", "time": 10244.0, "unit": "ns",
           "allocs_per_op": 11.0, "bytes_per_op": 5456.0}
    unnamed = {**row, "benchmark": "Benchmark404", "measures": None, "time": 62.58,
               "allocs_per_op": 0.0, "bytes_per_op": None}
    out = _rendered({"benchmarks": {"runs": [bench_run], "rows": [row, unnamed], "total": 2}})
    assert 'Benchmarks ("go bench" at abcdef1)' in out
    assert "BenchmarkGetMapFromFormData/Large_Bracket -> context.go::getMapFromFormData" in out
    assert "10,244 ns/op, 11 allocs/op, 5,456 B/op" in out
    assert "Benchmark404\n" in out and "62.6 ns/op, 0 allocs/op" in out
    assert "of the run" not in out


def test_an_import_row_reads_as_the_file_and_import_time() -> None:
    row = {**ROW, "symbol_id": "src/app.py::<module>", "lines": None, "label": "import time"}
    out = _rendered({"measured": {"runs": [RUN], "rows": [row]}})
    assert "1. src/app.py import time" in out
    assert "<module>" not in out


def test_production_statements_say_their_window_and_what_is_on_no_function() -> None:
    from repowise.core.evidence.action_row import action_row, statement_facts
    from repowise.core.evidence.measured_fold import StatementCost

    cost = StatementCost("src/app.py::save", "s1", 1, 0.6, 600.0, 40, 2)
    row = action_row(
        cost, workload_name="orders-db", workload_class="production",
        statements=statement_facts(cost),
    ).as_dict()
    run = {
        "run_id": "s1", "workload_class": "production", "workload_name": "orders-db",
        "commit": "d" * 40, "window_start": "2026-11-01T00:00:00+00:00",
        "window_end": "2026-11-26T00:00:00+00:00", "total_ms": 1000.0, "statements": 5,
        "unattributed": {"ambiguous": {"statements": 2, "total_ms": 300.0, "share": 0.3}},
    }
    out = _rendered({"statements": {"runs": [run], "rows": [row], "total": 1}})
    assert (
        'Production statements (production "orders-db" at ddddddd over 2026-11-01 to '
        "2026-11-26: 1,000 ms in 5 statements, 30% on no single function)"
    ) in out
    assert "60% of the window's statement time · 40 calls · 15 ms mean · 2 statements" in out
    assert "verify: compare this row in the next orders-db window" in out


def test_production_routes_say_their_window_requests_and_what_is_on_no_handler() -> None:
    from repowise.core.evidence.action_row import action_row, route_facts
    from repowise.core.evidence.measured_fold import RouteCost

    cost = RouteCost("src/app.py::serve", "q1", 1, 0.6, 600.0, 40, 15.0, None, 30.0, ("GET /x",))
    row = action_row(
        cost, workload_name="api", workload_class="production", routes=route_facts(cost)
    ).as_dict()
    run = {
        "run_id": "q1", "workload_class": "production", "workload_name": "api",
        "commit": None, "window_start": "2026-11-01T00:00:00+00:00",
        "window_end": "2026-11-26T00:00:00+00:00", "total_ms": 1000.0, "requests": 50,
        "routes": 3, "unattributed": {"unjoined": {"routes": 2, "total_ms": 400.0, "share": 0.4}},
    }
    out = _rendered({"routes": {"runs": [run], "rows": [row], "total": 1}})
    assert (
        'Production routes (production "api" over 2026-11-01 to 2026-11-26: 1,000 ms in 50 '
        "requests to 3 routes, 40% on no single function)"
    ) in out
    assert "GET /x · 60% of the window's request time · 40 requests · 15 ms mean · 30 ms p95" in out
