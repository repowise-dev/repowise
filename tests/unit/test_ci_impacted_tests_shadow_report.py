"""The script that sums up the CI test selection records."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
_path = ROOT / "scripts" / "impacted_tests_shadow_report.py"
_spec = importlib.util.spec_from_file_location("impacted_tests_shadow_report", _path)
report = importlib.util.module_from_spec(_spec)
sys.modules["impacted_tests_shadow_report"] = report
_spec.loader.exec_module(report)


def _record(run_id: int, sha: str, **fields) -> dict:
    base = {
        "run_id": str(run_id),
        "event": "pull_request",
        "sha": sha,
        "pr": run_id,
        "complete": True,
        "run_all": False,
        "total_seconds": 100.0,
        "estimated_seconds_saved": 50.0,
        "failing_files": [],
        "missed": [],
        "first_failing_rank": None,
        "order_length": 10,
    }
    return {**base, **fields}


def _write(root: pathlib.Path, *records: dict) -> None:
    for r in records:
        folder = root / r["run_id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "impacted-shadow.json").write_text(json.dumps(r), encoding="utf-8")


def test_a_rerun_counts_once_and_incomplete_records_are_left_out(tmp_path) -> None:
    _write(
        tmp_path,
        _record(1, "a", estimated_seconds_saved=10.0),
        _record(2, "a", estimated_seconds_saved=30.0),
        _record(3, "b", complete=False),
        _record(4, "c", event="push"),
    )
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "impacted-shadow.json").write_text("{not json", encoding="utf-8")

    records = report.load(tmp_path)

    assert [r["run_id"] for r in records] == ["2"]
    assert [r["run_id"] for r in report.load(tmp_path, ("pull_request", "push"))] == ["2", "4"]


def test_summary_counts_full_runs_saving_and_first_failure_position() -> None:
    records = [
        _record(1, "a", run_all=True, estimated_seconds_saved=0.0),
        _record(2, "b", failing_files=["t/test_x.py"], first_failing_rank=1, order_length=10),
        _record(3, "c", failing_files=["t/test_y.py"], first_failing_rank=5, order_length=10),
    ]

    s = report.summarize(records)

    assert (s["runs"], s["run_all"], s["seconds_saved"], s["total_seconds"]) == (3, 1, 100.0, 300.0)
    assert (s["failing_runs"], s["failing_runs_caught"], s["ordered_runs"]) == (2, 2, 2)
    assert s["unordered_runs"] == 0
    assert s["first_failure_rank_median"] == 3
    assert s["first_failure_position_median"] == 0.3
    assert s["first_failure_position_p75"] == 0.4


def test_a_miss_that_failed_on_another_pull_request_reads_as_likely_flaky() -> None:
    records = [
        _record(1, "a", failing_files=["t/test_slow.py"], missed=["t/test_slow.py"]),
        _record(2, "b", failing_files=["t/test_slow.py"]),
        _record(3, "c", failing_files=["t/test_new.py"], missed=["t/test_new.py"]),
    ]

    s = report.summarize(records)
    text = report.render(s)

    assert [(m["file"], m["also_failed_on"]) for m in s["misses"]] == [
        ("t/test_slow.py", ["2"]),
        ("t/test_new.py", []),
    ]
    assert (
        "t/test_slow.py (PR 1, a); it also failed on 1 other change(s), so flaky or already failing."
        in text
    )
    assert "t/test_new.py (PR 3, c)." in text


def test_a_failing_run_missing_from_the_order_is_counted_not_dropped() -> None:
    records = [
        _record(1, "a", failing_files=["t/test_x.py"], first_failing_rank=2, order_length=10),
        _record(2, "b", failing_files=["t/test_y.py"], missed=["t/test_y.py"]),
    ]

    s = report.summarize(records)
    text = report.render(s)

    assert (s["failing_runs"], s["ordered_runs"], s["unordered_runs"]) == (2, 1, 1)
    assert "over 1 runs" in text
    assert "No failing file in the prioritized order: 1 of 2 runs with a failing test" in text


def test_no_records_reports_without_an_ordering_line(tmp_path) -> None:
    s = report.summarize(report.load(tmp_path))

    assert s["runs"] == 0 and s["ordered_runs"] == 0 and s["unordered_runs"] == 0
    text = report.render(s)
    assert "First failing file" not in text and "No failing file" not in text
