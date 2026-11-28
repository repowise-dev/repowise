"""``repowise.core.perf_check``: only exact values judge a change; timing is never read."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.perf_check import (
    compare,
    load_snapshot,
    newest_runs,
    save_snapshot,
    snapshot,
    snapshot_path,
)
from repowise.core.perf_measure import BaselineError, baseline_path


def _run(
    run_id: str, workload: str = "go bench results ./strs", kind: str = "benchmark", at: int = 1
):
    return SimpleNamespace(
        id=run_id, kind=kind, workload_name=workload, commit_sha="a" * 40, dirty=False,
        ingested_at=datetime(2026, 11, 19, at, tzinfo=UTC),
    )


def _row(run_id: str, key: str, allocs: float | None, median: float = 50.0):
    return SimpleNamespace(run_id=run_id, benchmark_key=key, allocs_per_op=allocs, median=median)


def _snap(run_id: str, values: dict[str, float | None], median: float = 50.0, at: int = 1) -> dict:
    newest = newest_runs([_run(run_id, at=at)])
    return snapshot(newest, [_row(run_id, k, v, median) for k, v in values.items()])


def test_the_newest_benchmark_run_of_each_workload_is_read() -> None:
    runs = [_run("p", kind="profile"), _run("new"), _run("old"), _run("other", workload="w2")]
    assert {w: r.id for w, r in newest_runs(runs).items()} == {
        "go bench results ./strs": "new", "w2": "other",
    }
    assert list(newest_runs(runs, "w2")) == ["w2"]


def test_a_workload_without_exact_values_has_nothing_to_judge() -> None:
    snap = _snap("r1", {"BenchmarkJoin": None})
    assert snap["workloads"] == {}
    assert compare(snap, _snap("r2", {"BenchmarkJoin": 1}))["verdict"] == "cannot_judge"


def test_more_allocations_regress_and_timing_is_never_read() -> None:
    base = _snap("r1", {"BenchmarkJoin": 1, "BenchmarkConcat": 4}, median=50)
    # Ten times slower but allocating the same: passes.
    same = compare(base, _snap("r2", {"BenchmarkJoin": 1, "BenchmarkConcat": 4}, median=500))
    assert (same["verdict"], same["compared"], same["regressed"]) == ("passed", 2, [])

    result = compare(base, _snap("r3", {"BenchmarkJoin": 0, "BenchmarkConcat": 6}))
    assert result["verdict"] == "regressed"
    assert [(c["benchmark"], c["base"], c["head"]) for c in result["regressed"]] == [
        ("BenchmarkConcat", 4, 6),
    ]
    assert [c["benchmark"] for c in result["improved"]] == ["BenchmarkJoin"]
    # A benchmark that is gone is listed, never compared as unchanged.
    fewer = compare(base, _snap("r4", {"BenchmarkJoin": 1}))
    assert (fewer["compared"], fewer["dropped"]) == (
        1, [{"workload": "go bench results ./strs", "benchmark": "BenchmarkConcat"}],
    )


def test_a_workload_not_run_again_is_not_judged() -> None:
    base = _snap("r1", {"BenchmarkJoin": 1})
    result = compare(base, base)
    assert (result["verdict"], result["not_rerun"]) == ("cannot_judge", ["go bench results ./strs"])
    assert "run and ingested again" in result["reason"]
    # The same output ingested again restamps the run: it was run again.
    again = compare(base, _snap("r1", {"BenchmarkJoin": 1}, at=2))
    assert (again["verdict"], again["compared"]) == ("passed", 1)
    gone = compare(base, {"version": 1, "workloads": {}})
    assert (gone["verdict"], gone["missing"]) == ("cannot_judge", ["go bench results ./strs"])


def test_the_baseline_sits_beside_perf_measures_without_replacing_it(tmp_path: Path) -> None:
    snap = _snap("r1", {"BenchmarkJoin": 1})
    path = save_snapshot(tmp_path, "base", snap)
    assert path == snapshot_path(tmp_path, "base") == tmp_path / "perf" / "exact" / "base.json"
    # No perf measure name reaches it: a name holds no "/".
    assert path not in {baseline_path(tmp_path, n) for n in ("base", "base.exact", "exact")}
    assert load_snapshot(tmp_path, "base") == snap
    with pytest.raises(BaselineError, match="perf check --save other"):
        load_snapshot(tmp_path, "other")
    with pytest.raises(BaselineError, match="must be"):
        snapshot_path(tmp_path, "../x")
    baseline_path(tmp_path, "old").write_text('{"version": 1, "samples": [1, 1, 1, 1]}')
    path.with_name("old.json").write_text('{"version": 9}')
    with pytest.raises(BaselineError, match="save it again"):
        load_snapshot(tmp_path, "old")


@pytest.mark.parametrize("workload", [
    {"benchmarks": {}},
    {"run_id": "r1"},
    {"run_id": "r1", "benchmarks": {"BenchmarkJoin": 1}},
    {"run_id": "r1", "benchmarks": {"BenchmarkJoin": {"allocs_per_op": "1"}}},
])
def test_a_hand_edited_baseline_is_refused_not_crashed_on(tmp_path: Path, workload) -> None:
    path = snapshot_path(tmp_path, "edited")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "workloads": {"w": workload}}))
    with pytest.raises(BaselineError, match="save it again"):
        load_snapshot(tmp_path, "edited")
