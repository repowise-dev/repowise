"""The ``symbol_hotness`` rules: ranked, lead candidates, ordering only, freshness and origin."""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.analysis.lineage import FunctionBody
from repowise.core.evidence.hotness import (
    TIERS,
    CostFacts,
    CurrentFunctions,
    HotnessRules,
    RunFacts,
    derive_hotness,
    hotness_rules,
)

HEAD = "a" * 40
OLD = "b" * 40
RULES = HotnessRules(top_n=10, min_samples=30, lead_min_share=0.025, lead_min_lower_bound=0.02,
                     lead_min_runs=2)


def _run(run_id: str = "r1", *, cls: str = "entry_point", commit: str = HEAD, total: float | None = 1000,
         samples: int | None = None, unit: str = "samples", explained: float | None = 400,
         day: int = 1, split: tuple[float, float, float] | None = None) -> RunFacts:
    repo_self, waits, libraries = split or (None, None, None)
    return RunFacts(
        id=run_id, workload_name=f"w-{run_id}", workload_class=cls, value_kind="wall", unit=unit,
        commit_sha=commit, measured_at=datetime(2026, 11, day, tzinfo=UTC), total_value=total,
        explained_value=explained, total_samples=samples, repo_self_value=repo_self,
        wait_value=waits, library_value=libraries,
    )


def _cost(name: str, cost: float, *, total: float | None = None, path: str = "src/m.py",
          digest: str | None = "d", lineage: str | None = None, samples: int | None = None,
          calls: int | None = None) -> CostFacts:
    """A row whose self time (the cost) is *cost*; inclusive is twice that unless given."""
    return CostFacts(
        symbol_id=f"{path}::{name}", lineage_id=lineage, body_digest=digest,
        total_value=total if total is not None else cost * 2, self_value=cost,
        total_samples=None if samples is None else samples * 2, call_count=calls,
        self_samples=samples,
    )


def _current(*ids: str, digest: str = "d", lineage: dict[str, str] | None = None) -> CurrentFunctions:
    lineage = lineage or {}
    return CurrentFunctions.from_bodies(
        FunctionBody(sid, digest, 40, lineage.get(sid)) for sid in ids
    )


def _origins(*paths: str, origin: str = "production") -> dict[str, str]:
    return dict.fromkeys(paths, origin)


def _derive(runs, costs, current=None, origins=None, head=HEAD, rules=RULES):
    ids = {c.symbol_id for rows in costs.values() for c in rows}
    current = current if current is not None else _current(*ids)
    origins = origins if origins is not None else _origins(*{i.split("::")[0] for i in ids})
    rows = derive_hotness(runs, costs, current, origins, head_commit=head, rules=rules).symbols
    return {r["symbol_id"]: r for r in rows}


def test_tier_names_never_say_hot() -> None:
    assert TIERS == ("measured", "ranked", "lead_candidate")
    assert not any("hot" in tier or "cold" in tier for tier in TIERS)


def test_rules_read_the_evidence_settings() -> None:
    assert hotness_rules(None) == RULES
    custom = hotness_rules({"evidence": {"rank_top_n": 3, "lead_min_share": 0.1}})
    assert (custom.top_n, custom.lead_min_share, custom.min_samples) == (3, 0.1, 30)


def test_top_functions_with_enough_samples_are_ranked_else_measured() -> None:
    costs = [_cost(f"f{i}", 100 - i) for i in range(12)] + [_cost("thin", 20)]
    # Each under 5% of the run: ranked at most, never a lead from one run.
    rows = _derive([_run(total=10000)], {"r1": costs})
    assert (rows["src/m.py::f0"]["rank"], rows["src/m.py::f0"]["tier"]) == (1, "ranked")
    assert rows["src/m.py::f9"]["rank"] == 10
    assert rows["src/m.py::f9"]["tier"] == "ranked"
    # Outside the top 10.
    assert (rows["src/m.py::f10"]["rank"], rows["src/m.py::f10"]["tier"]) == (11, "measured")
    # Not enough samples in repo code: the count stays for the reader to say so.
    assert (rows["src/m.py::thin"]["tier"], rows["src/m.py::thin"]["samples"]) == ("measured", 20)


def test_the_row_carries_the_run_summary_and_an_interval() -> None:
    run = _run(split=(470, 300, 120))
    row = _derive([run], {"r1": [_cost("f", 100)]})["src/m.py::f"]
    assert (row["repo_self_share"], row["wait_share"], row["library_share"]) == (0.47, 0.3, 0.12)
    # Samples and the interval are the cost's: self, not inclusive.
    assert (row["samples"], row["run_samples"]) == (100, 1000)
    assert (row["self_share"], row["total_share"]) == (0.1, 0.2)
    assert row["share_low"] < 0.1 < row["share_high"]
    assert (row["workload_name"], row["workload_class"], row["run_id"]) == ("w-r1", "entry_point",
                                                                           "r1")
    assert (row["evidence_commit_sha"], row["freshness"], row["ordering_only"]) == (HEAD, "current",
                                                                                  False)
    assert row["run_count"] == 1


def test_a_time_unit_run_uses_its_counted_samples() -> None:
    run = _run(unit="ms", total=5000.0, samples=500)
    row = _derive([run], {"r1": [_cost("f", 1000.0, samples=100)]})["src/m.py::f"]
    assert (row["samples"], row["run_samples"]) == (100, 500)
    assert row["share_low"] is not None


def test_the_interval_brackets_the_time_share_when_samples_weigh_unequally() -> None:
    # A sampler's samples carry unequal time: 3.6% of the time on 4% of the samples.
    run = _run(unit="ms", total=10000.0, samples=50000)
    row = _derive([run], {"r1": [_cost("f", 356.0, samples=2000)]})["src/m.py::f"]
    assert row["self_share"] == 0.0356
    assert row["share_low"] < row["self_share"] < row["share_high"]
    # The counts' relative width is kept.
    assert row["share_low"] > 0.0356 * 0.9
    assert row["share_high"] < 0.0356 * 1.1


def test_a_run_without_a_time_total_never_clears_the_share_rule() -> None:
    run = _run(unit="ms", total=None, samples=1000)
    row = _derive([run], {"r1": [_cost("f", 400.0, samples=400)]})["src/m.py::f"]
    assert (row["tier"], row["self_share"]) == ("ranked", None)


def test_a_function_under_the_sample_floor_takes_no_place() -> None:
    # Costlier in time than "next" but too few samples: the places run 1, 2.
    run = _run(unit="ms", total=10000.0, samples=10000)
    costs = [_cost("top", 900.0, samples=900), _cost("thin", 500.0, samples=10),
             _cost("next", 400.0, samples=400)]
    rows = _derive([run], {"r1": costs})
    assert [rows[f"src/m.py::{n}"]["rank"] for n in ("top", "thin", "next")] == [1, None, 2]
    assert rows["src/m.py::thin"]["tier"] == "measured"


def test_a_function_that_only_calls_things_ranks_below_the_one_doing_the_work() -> None:
    # The entry function is on every stack (inclusive 95%) and costs nothing itself.
    costs = [_cost("main", 1, total=950), _cost("dispatch", 2, total=900), _cost("scan", 120)]
    rows = _derive([_run(unit="s", total=1000.0)], {"r1": costs})
    assert [rows[f"src/m.py::{n}"]["rank"] for n in ("scan", "dispatch", "main")] == [1, 2, 3]
    assert rows["src/m.py::main"]["total_share"] == 0.95


def test_a_function_that_costs_nothing_itself_is_never_ranked() -> None:
    # Fewer costly functions than the top: the pure callers still take no place.
    costs = [_cost("leaf", 50, total=60), _cost("main", 0, total=100)]
    rows = _derive([_run(unit="s", total=100.0)], {"r1": costs})
    assert (rows["src/m.py::leaf"]["rank"], rows["src/m.py::leaf"]["tier"]) == (1, "ranked")
    assert (rows["src/m.py::main"]["rank"], rows["src/m.py::main"]["tier"]) == (None, "measured")


def test_a_stored_run_without_a_split_says_unknown() -> None:
    row = _derive([_run()], {"r1": [_cost("f", 100)]})["src/m.py::f"]
    assert row["repo_self_share"] is row["wait_share"] is row["library_share"] is None


def test_an_uncounted_run_ranks_by_order_without_an_interval() -> None:
    run = _run(unit="s", total=10.0)
    rows = _derive([run], {"r1": [_cost("f", 6.0), _cost("g", 1.0)]})
    assert rows["src/m.py::f"]["tier"] == "ranked"
    assert rows["src/m.py::f"]["samples"] is None
    assert rows["src/m.py::f"]["share_low"] is None


def test_a_big_share_in_a_class_that_may_lead_is_a_lead_candidate() -> None:
    rows = _derive([_run(total=4000)], {"r1": [_cost("big", 200), _cost("small", 40)]})
    assert rows["src/m.py::big"]["tier"] == "lead_candidate"
    # 1% of the run: ranked, not a lead.
    assert rows["src/m.py::small"]["tier"] == "ranked"


def test_unit_test_evidence_clears_the_share_rule_but_never_the_runs_rule() -> None:
    # Above the share bar: a measured cost, which readers keep apart as in tests.
    big = _derive([_run("r1", cls="unit_test")], {"r1": [_cost("big", 500)]})["src/m.py::big"]
    assert (big["tier"], big["workload_class"]) == ("lead_candidate", "unit_test")
    # Under it, ranked in two test runs is not enough.
    runs = [_run("r1", cls="unit_test", total=10000), _run("r2", cls="unit_test", total=10000)]
    costs = {"r1": [_cost("f", 100)], "r2": [_cost("f", 100)]}
    assert _derive(runs, costs)["src/m.py::f"]["tier"] == "ranked"


def test_a_leading_run_stays_the_basis_over_a_stronger_test_run() -> None:
    runs = [_run("r1", day=1, total=10000), _run("r2", cls="unit_test", day=2, total=10000)]
    costs = {"r1": [_cost("f", 100)], "r2": [_cost("f", 5000)]}
    row = _derive(runs, costs)["src/m.py::f"]
    assert (row["run_id"], row["tier"]) == ("r1", "ranked")


def test_ranked_in_two_runs_makes_a_lead_candidate() -> None:
    runs = [_run("r1", total=4000), _run("r2", day=2, total=4000)]
    costs = {"r1": [_cost("f", 40)], "r2": [_cost("f", 40)]}
    row = _derive(runs, costs)["src/m.py::f"]
    assert (row["tier"], row["run_count"]) == ("lead_candidate", 2)
    # The newest of the equal runs is the basis.
    assert row["run_id"] == "r2"
    one = _derive([_run("r1", total=4000)], {"r1": [_cost("f", 40)]})["src/m.py::f"]
    assert one["tier"] == "ranked"


def test_a_tracer_run_orders_only() -> None:
    costs = [_cost("f", 900, calls=3), _cost("g", 100, calls=1000)]
    rows = _derive([_run(unit="s", total=1.0)], {"r1": costs})
    f = rows["src/m.py::f"]
    assert (f["rank"], f["tier"], f["ordering_only"]) == (1, "ranked", True)
    assert f["total_share"] is f["self_share"] is f["share_low"] is None
    # Two tracer runs never make a lead either.
    two = _derive([_run("r1", unit="s"), _run("r2", unit="s")],
                  {"r1": [_cost("f", 9, calls=1)], "r2": [_cost("f", 9, calls=1)]})
    assert two["src/m.py::f"]["tier"] == "ranked"


def test_an_older_commit_with_the_same_body_counts_and_an_edited_body_is_stale() -> None:
    costs = {"r1": [_cost("same", 300, digest="d"), _cost("edited", 200, digest="old")]}
    rows = _derive([_run(commit=OLD)], costs)
    assert rows["src/m.py::same"]["freshness"] == "body_unchanged"
    assert rows["src/m.py::same"]["rank"] == 1
    edited = rows["src/m.py::edited"]
    assert (edited["freshness"], edited["rank"], edited["tier"]) == ("stale", None, "measured")


def test_an_older_commit_without_a_measured_body_is_stale() -> None:
    row = _derive([_run(commit=OLD)], {"r1": [_cost("f", 300, digest=None)]})["src/m.py::f"]
    assert row["freshness"] == "stale"


def test_fresh_evidence_is_the_basis_over_stale() -> None:
    runs = [_run("old", commit=OLD, day=5), _run("new", day=1)]
    costs = {"old": [_cost("f", 900, digest="x")], "new": [_cost("f", 50)]}
    row = _derive(runs, costs)["src/m.py::f"]
    assert (row["run_id"], row["freshness"]) == ("new", "current")


def test_a_moved_function_follows_its_lineage() -> None:
    cost = _cost("f", 300, path="src/old.py", lineage="L1")
    current = _current("src/new.py::f", lineage={"src/new.py::f": "L1"})
    rows = _derive([_run()], {"r1": [cost]}, current=current, origins=_origins("src/new.py"))
    assert list(rows) == ["src/new.py::f"]
    assert rows["src/new.py::f"]["lineage_id"] == "L1"


def test_only_production_code_is_listed_and_the_rest_is_counted() -> None:
    costs = [_cost("test_f", 900, path="tests/test_m.py"), _cost("f", 300),
             _cost("gen", 200, path="gen/pb.py")]
    origins = {"tests/test_m.py": "test", "src/m.py": "production", "gen/pb.py": "generated"}
    rows = _derive([_run(cls="unit_test")], {"r1": costs}, origins=origins)
    assert list(rows) == ["src/m.py::f"]
    # The test function took no ranking slot.
    assert rows["src/m.py::f"]["rank"] == 1
    assert rows["src/m.py::f"]["run_unlisted"] == 2


def test_no_runs_no_rows() -> None:
    assert derive_hotness([], {}, _current(), {}, head_commit=HEAD, rules=RULES).symbols == []


def test_an_unknown_indexed_commit_lets_only_the_stored_body_decide() -> None:
    costs = {"r1": [_cost("same", 300, digest="d"), _cost("unknown", 200, digest=None)]}
    rows = _derive([_run()], costs, head=None)
    assert rows["src/m.py::same"]["freshness"] == "body_unchanged"
    assert rows["src/m.py::unknown"]["freshness"] == "stale"


def test_the_unlisted_count_from_ingest_wins_over_the_rows_kept() -> None:
    run = RunFacts(**{**_run().__dict__, "unlisted_symbols": 7})
    row = _derive([run], {"r1": [_cost("f", 300)]})["src/m.py::f"]
    assert row["run_unlisted"] == 7


def _workloads(runs, costs):
    ids = {c.symbol_id for rows in costs.values() for c in rows}
    hotness = derive_hotness(runs, costs, _current(*ids), _origins("src/m.py"), head_commit=HEAD,
                             rules=RULES)
    return hotness.workloads


def test_each_workload_lists_its_own_ranked_functions_from_its_newest_run() -> None:
    # Two workloads rank the same function; one workload has a superseded run.
    runs = [_run("init"), _run("update-old", day=1), _run("update", day=2)]
    runs = [RunFacts(**{**r.__dict__, "workload_name": "update"}) if r.id.startswith("update")
            else r for r in runs]
    costs = {
        "init": [_cost("parse", 200), _cost("walk", 100)],
        "update-old": [_cost("parse", 300)],
        "update": [_cost("parse", 50), _cost("scan", 400)],
    }
    got = [(w["run_id"], w["rank"], w["symbol_id"].split("::")[1]) for w in _workloads(runs, costs)]
    assert got == [
        ("init", 1, "parse"), ("init", 2, "walk"), ("update", 1, "scan"), ("update", 2, "parse"),
    ]
    # The symbol view still keeps one basis row per function.
    hotness = derive_hotness(runs, costs, _current(*{c.symbol_id for r in costs.values() for c in r}),
                             _origins("src/m.py"), head_commit=HEAD, rules=RULES)
    assert sorted(r["symbol_id"] for r in hotness.symbols) == [
        "src/m.py::parse", "src/m.py::scan", "src/m.py::walk",
    ]


def test_a_workload_row_carries_its_cost_and_never_a_measured_row() -> None:
    costs = {"r1": [_cost("f", 100, samples=100), _cost("thin", 5, samples=5),
                    _cost("edited", 300, digest="old")]}
    (row,) = _workloads([_run(commit=OLD)], {"r1": [costs["r1"][0]]})
    assert row["freshness"] == "body_unchanged"
    rows = {w["symbol_id"]: w for w in _workloads([_run()], costs)}
    # Under the sample floor: measured, not listed.
    assert "src/m.py::thin" not in rows
    f = rows["src/m.py::f"]
    assert (f["self_share"], f["total_share"], f["samples"], f["ordering_only"]) == (
        0.1, 0.2, 100, False,
    )
    assert f["share_low"] < 0.1 < f["share_high"]
