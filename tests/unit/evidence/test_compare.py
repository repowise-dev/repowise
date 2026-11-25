"""``evidence.compare``: what got slower against a band of earlier runs, and which commit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from repowise.core.evidence.compare import (
    Cause,
    History,
    cause_words,
    compare_runs,
    comparison_line,
    comparison_pairs,
    comparison_payload,
    runs_to_read,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
BASE, HEAD = "a" * 40, "b" * 40
BENCH = "gin.BenchmarkOneRoute"
BENCH_FN = "bench_test.go::BenchmarkOneRoute"
GET_VALUE = "tree.go::node::getValue"


def _run(i: int, commit: str, *, kind: str = "benchmark", workload: str = "go bench", **over):
    return NS(
        id=f"r{i}", kind=kind, workload_name=workload, workload_class="benchmark",
        environment="ci", commit_sha=commit, measured_at=T0 + timedelta(hours=i),
        ingested_at=None, mapping_partial=False, value_kind=None, unit="ns", total_value=None,
        **over,
    )


def _bench(median: float, *, key: str = BENCH, spread: float = 0.01, allocs=0.0, **over):
    return NS(
        benchmark_key=key, symbol_id=over.pop("symbol_id", BENCH_FN), unit="ns",
        lower_is_better=over.pop("lower_is_better", True), median=median, min=median * (1 - spread),
        max=median * (1 + spread), allocs_per_op=allocs, bytes_per_op=0.0, **over,
    )


def _series(band: list[float], current: list[float], **bench) -> tuple[list, dict]:
    runs, rows = [], {}
    for i, value in enumerate([*band, *current]):
        run = _run(i, BASE if i < len(band) else HEAD)
        runs.append(run)
        rows[run.id] = [_bench(value, **bench)]
    return runs, rows


def test_same_code_against_a_band_is_within_noise() -> None:
    runs, rows = _series([100, 103, 98], [104])
    [found] = compare_runs(runs, rows)
    assert found.verdict == "within_noise"
    assert found.slower == ()
    assert comparison_line(found) == '"go bench": within noise of its 3 earlier runs.'


def test_a_big_slowdown_against_a_band_is_slower() -> None:
    runs, rows = _series([100, 103, 98], [160])
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    assert (found.band_runs, found.current_runs) == (3, 1)
    assert (found.base_commit, found.head_commit) == (BASE, HEAD)
    [item] = found.slower
    assert (item.metric, item.name, item.before, item.after) == ("time", BENCH, 100, 160)
    assert round(item.ratio, 2) == 1.6


def test_one_run_needs_the_pair_floor_and_pooled_runs_the_band_floor() -> None:
    # 15% from one run is within what one laptop run drifts...
    runs, rows = _series([100, 101, 99], [115])
    assert compare_runs(runs, rows)[0].verdict == "within_noise"
    # ...but three runs of the new commit pool their samples.
    runs, rows = _series([100, 101, 99], [115, 116, 114])
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    assert found.current_runs == 3


def test_the_band_spread_raises_the_bar() -> None:
    # A band that itself moved 40% does not flag a 30% run.
    runs, rows = _series([100, 140, 100], [130, 131])
    assert compare_runs(runs, rows)[0].verdict == "within_noise"


def test_overlapping_samples_never_flag() -> None:
    runs, rows = _series([100, 101, 99], [160], spread=0.4)
    assert compare_runs(runs, rows)[0].verdict == "within_noise"


def test_under_three_earlier_runs_time_only_looks_slower() -> None:
    runs, rows = _series([100], [160])
    [found] = compare_runs(runs, rows)
    assert found.verdict == "looks_slower"
    assert "confirm with `repowise perf measure`" in comparison_line(found)
    runs, rows = _series([100, 101], [115])
    assert compare_runs(runs, rows)[0].verdict == "within_noise"


def test_an_allocation_flags_on_any_increase_whatever_the_band() -> None:
    runs, rows = _series([100], [101])
    rows[runs[-1].id] = [_bench(101, allocs=1.0)]
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    [item] = found.slower
    assert (item.metric, item.before, item.after) == ("allocs_per_op", 0.0, 1.0)
    assert "BenchmarkOneRoute 0 to 1 allocs/op" in comparison_line(found)


def test_higher_is_better_reads_the_other_way() -> None:
    runs, rows = _series([100, 101, 99], [60], lower_is_better=False)
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    assert found.slower[0].ratio > 1.6


def test_runs_at_the_current_commit_never_join_the_band() -> None:
    runs, rows = _series([100, 101, 99], [100])
    # An older run of the current commit, between the band runs.
    late = _run(9, HEAD)
    late.measured_at = T0 + timedelta(minutes=90)
    runs.append(late)
    rows[late.id] = [_bench(500)]
    [found] = compare_runs(runs, rows)
    assert found.band_runs == 3
    assert found.verdict == "within_noise"


def test_no_earlier_run_at_another_commit_means_no_comparison() -> None:
    runs = [_run(0, HEAD), _run(1, HEAD)]
    rows = {r.id: [_bench(100)] for r in runs}
    assert compare_runs(runs, rows) == []
    assert comparison_pairs(runs) == []


def test_a_partially_mapped_run_never_counts() -> None:
    runs, rows = _series([100, 101, 99], [160])
    runs[-1].mapping_partial = True
    # The newest usable run is a band run at BASE: nothing earlier at another commit.
    assert compare_runs(runs, rows) == []


def test_a_family_reports_its_geomean_beside_a_flagged_member() -> None:
    runs, rows = [], {}
    for i, (a, b) in enumerate([(100, 100), (101, 100), (99, 101), (160, 102)]):
        run = _run(i, BASE if i < 3 else HEAD)
        runs.append(run)
        rows[run.id] = [_bench(a, key="pkg.BenchmarkForm/small"), _bench(b, key="pkg.BenchmarkForm/big")]
    [found] = compare_runs(runs, rows)
    [item] = found.slower
    assert item.name == "pkg.BenchmarkForm/small"
    assert item.family_geomean is not None and 1.2 < item.family_geomean < 1.3
    assert "BenchmarkForm/small" in comparison_line(found)
    assert "pkg." not in comparison_line(found)


def test_the_cause_names_the_commit_that_changed_a_function_the_benchmark_reached() -> None:
    runs, rows = _series([100, 101, 99], [160])
    other = "c" * 40
    history = History(
        ranges={(BASE, HEAD): [(HEAD, "tree: walk the path again (#9001)"), (other, "docs: typo")]},
        touched={HEAD: {GET_VALUE}, other: {"README.md::x"}},
        callees={BENCH_FN: {"gin.go::Engine::ServeHTTP", GET_VALUE}},
    )
    [found] = compare_runs(runs, rows, history)
    assert found.cause is not None
    assert found.cause.commits_in_range == 2
    [commit] = found.cause.commits
    assert (commit.sha, commit.pr, commit.functions) == (HEAD, 9001, (GET_VALUE,))
    line = comparison_line(found)
    assert 'tree.go::node::getValue in bbbbbbb "tree: walk the path again (#9001)"' in line
    assert "(#9001) (#9001)" not in line
    assert comparison_payload(found)["cause"]["commits"][0]["functions"] == [GET_VALUE]


def test_a_benchmark_profile_says_what_the_benchmarks_ran() -> None:
    runs, rows = _series([100, 101, 99], [160], symbol_id=None)
    profile = _run(7, HEAD, kind="profile", workload="go bench profile")
    runs.append(profile)
    rows[profile.id] = [NS(symbol_id=GET_VALUE, self_value=5.0)]
    assert profile.id in runs_to_read(runs)
    history = History(ranges={(BASE, HEAD): [(HEAD, "plant")]}, touched={HEAD: {GET_VALUE}})
    found = [c for c in compare_runs(runs, rows, history) if c.kind == "benchmark"][0]
    assert found.cause.commits[0].functions == (GET_VALUE,)


def test_without_a_changed_reached_function_only_the_range_is_said() -> None:
    runs, rows = _series([100, 101, 99], [160])
    history = History(ranges={(BASE, HEAD): [(HEAD, "docs")]}, touched={HEAD: {"x.go::other"}})
    [found] = compare_runs(runs, rows, history)
    assert found.cause.commits == ()
    assert cause_words(found.cause) == (
        "since aaaaaaa..bbbbbbb, 1 commit; no commit in it changed a function it ran"
    )
    # A range the host could not list says only the span.
    assert cause_words(Cause(BASE, HEAD, None)) == (
        "since aaaaaaa..bbbbbbb; no commit in it changed a function it ran"
    )


def test_within_noise_reads_no_history() -> None:
    runs, rows = _series([100, 101, 99], [100])
    [found] = compare_runs(runs, rows, History(ranges={(BASE, HEAD): [(HEAD, "x")]}))
    assert found.cause is None


def _profile_series(band: list[float], current: list[float], *, value_kind: str = "cpu", cls="entry_point"):
    runs, rows = [], {}
    for i, value in enumerate([*band, *current]):
        run = _run(i, BASE if i < len(band) else HEAD, kind="profile", workload="cli")
        run.workload_class, run.value_kind, run.unit, run.total_value = cls, value_kind, "ms", 10_000.0
        runs.append(run)
        rows[run.id] = [NS(symbol_id=GET_VALUE, self_value=value), NS(symbol_id="a.py::cold", self_value=50.0)]
    return runs, rows


def test_a_profile_compares_cpu_self_seconds_against_three_runs() -> None:
    runs, rows = _profile_series([1000, 1050, 980], [2000])
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    [item] = found.slower
    assert (item.metric, item.name, item.unit) == ("self_seconds", GET_VALUE, "s")
    assert (item.before, item.after) == (1.0, 2.0)
    assert "tree.go::node::getValue 2.00x (1 s to 2 s)" in comparison_line(found)


def test_a_profile_never_compares_wall_time_benchmarks_or_a_short_band() -> None:
    assert compare_runs(*_profile_series([1000, 1050, 980], [2000], value_kind="wall")) == []
    assert compare_runs(*_profile_series([1000, 1050, 980], [2000], cls="benchmark")) == []
    assert compare_runs(*_profile_series([1000, 1050], [2000])) == []


def _suite_series(band: list[float], current: list[float]):
    runs, rows = [], {}
    for i, total in enumerate([*band, *current]):
        run = _run(i, BASE if i < len(band) else HEAD, kind="test_timing", workload="tests")
        runs.append(run)
        rows[run.id] = [
            NS(test_id="t.py::a", duration_s=total / 2),
            NS(test_id="t.py::b", duration_s=total / 2),
        ]
    # A test only the new run has never counts.
    rows[runs[-1].id].append(NS(test_id="t.py::new", duration_s=500.0))
    return runs, rows


def test_a_suite_compares_the_seconds_of_the_tests_every_run_has() -> None:
    [found] = compare_runs(*_suite_series([100, 102, 98], [180]))
    assert found.verdict == "slower"
    [item] = found.slower
    assert (item.metric, item.name, item.before, item.after) == ("suite_seconds", "2 tests", 100, 180)


def test_a_short_suite_stays_silent() -> None:
    [found] = compare_runs(*_suite_series([10, 11, 9], [30]))
    assert found.verdict == "within_noise"


def test_query_counts_flag_on_any_increase() -> None:
    runs, rows = [], {}
    for i, count in enumerate([3, 3, 4]):
        run = _run(i, BASE if i < 2 else HEAD, kind="query_count", workload="api")
        runs.append(run)
        rows[run.id] = [NS(scope_kind="test", scope_id="t::list", site_symbol_id="db.py::load", query_count=count)]
    [found] = compare_runs(runs, rows)
    assert found.verdict == "slower"
    assert "test t::list 3 to 4 statements" in comparison_line(found)


def test_slower_workloads_lead() -> None:
    quiet_runs, quiet_rows = _series([100, 101, 99], [100])
    loud_runs, loud_rows = _series([100, 101, 99], [200])
    for run in loud_runs:
        run.workload_name = "z bench"
        run.id = "z" + run.id
    loud_rows = {"z" + k: v for k, v in loud_rows.items()}
    found = compare_runs([*quiet_runs, *loud_runs], {**quiet_rows, **loud_rows})
    assert [c.workload for c in found] == ["z bench", "go bench"]
