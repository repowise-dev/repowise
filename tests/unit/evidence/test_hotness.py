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
RULES = HotnessRules(top_n=10, min_samples=30, lead_min_share=0.05, lead_min_lower_bound=0.02,
                     lead_min_runs=2)


def _run(run_id: str = "r1", *, cls: str = "entry_point", commit: str = HEAD, total: float = 1000,
         samples: int | None = None, unit: str = "samples", explained: float | None = 400,
         day: int = 1) -> RunFacts:
    return RunFacts(
        id=run_id, workload_name=f"w-{run_id}", workload_class=cls, value_kind="wall", unit=unit,
        commit_sha=commit, measured_at=datetime(2026, 11, day, tzinfo=UTC), total_value=total,
        explained_value=explained, total_samples=samples,
    )


def _cost(name: str, total: float, *, path: str = "src/m.py", digest: str | None = "d",
          lineage: str | None = None, samples: int | None = None,
          calls: int | None = None) -> CostFacts:
    return CostFacts(
        symbol_id=f"{path}::{name}", lineage_id=lineage, body_digest=digest, total_value=total,
        self_value=total / 2, total_samples=samples, call_count=calls,
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
    rows = derive_hotness(runs, costs, current, origins, head_commit=head, rules=rules)
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
    row = _derive([_run()], {"r1": [_cost("f", 100)]})["src/m.py::f"]
    assert row["explained_share"] == 0.4
    assert (row["samples"], row["run_samples"]) == (100, 1000)
    assert row["total_share"] == 0.1
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


def test_an_uncounted_run_ranks_by_order_without_an_interval() -> None:
    run = _run(unit="s", total=10.0)
    rows = _derive([run], {"r1": [_cost("f", 6.0), _cost("g", 1.0)]})
    assert rows["src/m.py::f"]["tier"] == "ranked"
    assert rows["src/m.py::f"]["samples"] is None
    assert rows["src/m.py::f"]["share_low"] is None


def test_a_big_share_in_a_class_that_may_lead_is_a_lead_candidate() -> None:
    rows = _derive([_run()], {"r1": [_cost("big", 200), _cost("small", 40)]})
    assert rows["src/m.py::big"]["tier"] == "lead_candidate"
    # 4% of samples: ranked, not a lead.
    assert rows["src/m.py::small"]["tier"] == "ranked"


def test_unit_test_evidence_is_never_a_lead_candidate() -> None:
    runs = [_run("r1", cls="unit_test"), _run("r2", cls="unit_test")]
    costs = {"r1": [_cost("big", 500)], "r2": [_cost("big", 500)]}
    row = _derive(runs, costs)["src/m.py::big"]
    assert row["tier"] == "ranked"
    assert row["workload_class"] == "unit_test"


def test_ranked_in_two_runs_makes_a_lead_candidate() -> None:
    runs = [_run("r1"), _run("r2", day=2)]
    costs = {"r1": [_cost("f", 40)], "r2": [_cost("f", 40)]}
    row = _derive(runs, costs)["src/m.py::f"]
    assert (row["tier"], row["run_count"]) == ("lead_candidate", 2)
    # The newest of the equal runs is the basis.
    assert row["run_id"] == "r2"
    one = _derive([_run("r1")], {"r1": [_cost("f", 40)]})["src/m.py::f"]
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
    assert derive_hotness([], {}, _current(), {}, head_commit=HEAD, rules=RULES) == []
