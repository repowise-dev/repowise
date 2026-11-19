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
    "owner": {"name": "Ada", "commit_pct": 0.8},
    "tests": ["tests/test_app.py::test_serve"],
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
        "owner Ada" in out
    )
    assert "94% of the run" not in out
    assert "nearest test tests/test_app.py::test_serve" in out
    assert "tests not mapped · static cause perf3_x" in out
    assert not re.search(r"\b(hot|cold)\b", out, re.IGNORECASE)


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
