"""The CI script that compares a recorded test selection with the full run."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
_path = ROOT / "scripts" / "impacted_tests_shadow.py"
_spec = importlib.util.spec_from_file_location("impacted_tests_shadow", _path)
shadow = importlib.util.module_from_spec(_spec)
sys.modules["impacted_tests_shadow"] = shadow
_spec.loader.exec_module(shadow)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.unit.test_a" name="test_ok" file="tests/unit/test_a.py" line="3"/>
  <testcase classname="tests.unit.test_b.TestB" name="test_bad" file="tests/unit/test_b.py">
    <failure message="boom">trace</failure>
  </testcase>
  <testcase classname="tests.unit.sub.test_c.TestC" name="test_err"><error message="x"/></testcase>
  <testcase classname="" name="tests.unit.test_d"><error message="collection"/></testcase>
  <testcase classname="tests.unit.test_e" name="test_skip" file="tests/unit/test_e.py">
    <skipped message="no"/>
  </testcase>
</testsuite></testsuites>
"""

SELECTION = {
    "run_all": False,
    "reasons": [],
    "indexed_commit": "abc",
    "selected": {
        "test_files": ["tests/unit/test_a.py"],
        "tests": ["tests/unit/test_b.py::TestB::test_bad"],
        "always_run": ["tests/unit/test_e.py"],
    },
    "args": [
        "tests/unit/test_b.py::TestB::test_bad",
        "tests/unit/test_a.py",
        "tests/unit/test_e.py",
        "tests/unit/test_b.py::TestB::test_other",
        "tests/unit/sub/test_c.py",
        "tests/unit/test_d.py",
    ],
}


def test_module_file_drops_class_parts() -> None:
    assert shadow.module_file("tests.unit.sub.test_c.TestC") == "tests/unit/sub/test_c.py"
    assert shadow.module_file("tests.unit.test_d") == "tests/unit/test_d.py"


def test_read_junit_maps_cases_to_files() -> None:
    ran, failing = shadow.read_junit(JUNIT)
    assert ran == {
        "tests/unit/test_a.py",
        "tests/unit/test_b.py",
        "tests/unit/sub/test_c.py",
        "tests/unit/test_d.py",
        "tests/unit/test_e.py",
    }
    # A skipped case is not a failure; a collection error is.
    assert failing == {"tests/unit/test_b.py", "tests/unit/sub/test_c.py", "tests/unit/test_d.py"}


def test_run_order_and_first_rank() -> None:
    order = shadow.run_order(SELECTION["args"])
    assert order == [
        "tests/unit/test_b.py",
        "tests/unit/test_a.py",
        "tests/unit/test_e.py",
        "tests/unit/sub/test_c.py",
        "tests/unit/test_d.py",
    ]
    assert shadow.first_rank(order, {"tests/unit/test_d.py", "tests/unit/sub/test_c.py"}) == 4
    assert shadow.first_rank(order, {"tests/unit/other.py"}) is None


def test_record_lists_failures_outside_the_selection() -> None:
    meta = {"base": "b0", "selector_exit": 0, "selector_seconds": 12.5, "index_cache": "exact"}
    rec = shadow.build_record(SELECTION, meta, shadow.read_junit(JUNIT), {"pr": 7})
    assert rec["missed"] == ["tests/unit/sub/test_c.py", "tests/unit/test_d.py"]
    assert rec["selected_ran_files"] == 3
    assert rec["ran_files"] == 5
    assert rec["first_failing_rank"] == 1
    assert rec["order_length"] == 5
    assert rec["pr"] == 7 and rec["base"] == "b0" and rec["index_cache"] == "exact"
    # One JSON line per run, for aggregation.
    assert "\n" not in json.dumps(rec)
    text = shadow.render_summary(rec)
    assert "`tests/unit/test_d.py`" in text
    assert "recorded, not judged" in text


def test_run_all_misses_nothing() -> None:
    selection = {**SELECTION, "run_all": True, "reasons": ["A CI file changed."]}
    rec = shadow.build_record(selection, {}, shadow.read_junit(JUNIT), {})
    assert rec["missed"] == [] and rec["selected_ran_files"] == 5
    assert "run everything (1 reason)" in shadow.render_summary(rec)


def test_missing_inputs_still_make_a_record() -> None:
    rec = shadow.build_record(None, {"selector_exit": 2}, None, {})
    assert rec["has_selection"] is False and rec["has_junit"] is False
    text = shadow.render_summary(rec)
    assert "No selection was recorded (selector exit 2)" in text
    assert "No test report was found" in text


def test_main_never_fails(tmp_path, monkeypatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    bad = tmp_path / "selection.json"
    bad.write_text("{not json", encoding="utf-8")
    assert shadow.main(["--selection", str(bad), "--out", str(tmp_path / "r.json")]) == 0
    assert "could not run" in summary.read_text(encoding="utf-8")


def test_main_writes_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    sel = tmp_path / "selection.json"
    sel.write_text(json.dumps(SELECTION), encoding="utf-8")
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT, encoding="utf-8")
    out = tmp_path / "record.json"
    assert shadow.main(["--selection", str(sel), "--junit", str(junit), "--out", str(out)]) == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["missed"] == ["tests/unit/sub/test_c.py", "tests/unit/test_d.py"]
