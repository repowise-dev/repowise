"""The shared worth rule: which problems lead a default list and which wait, labelled."""

from __future__ import annotations

from typing import get_args

import pytest

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.worth import LOW_PRIORITY_LABEL, LowPriority, low_priority


@pytest.mark.parametrize(
    ("marker", "shape", "expected"),
    [
        # Far past a bar on code shape: worth doing first.
        ("complex_method", {"ccn": 47, "nloc": 191, "max_nesting": 5}, None),
        ("nested_complexity", {"ccn": 30, "nloc": 160, "max_nesting": 9}, None),
        ("large_method", {"ccn": 25, "nloc": 260, "max_nesting": 3}, None),
        # Past the bar, not by much.
        ("complex_method", {"ccn": 13, "nloc": 56, "max_nesting": 5}, "near_bar"),
        ("nested_complexity", {"ccn": 9, "nloc": 46, "max_nesting": 4}, "near_bar"),
        # Big, but one dispatch on one value, or one else-if chain.
        ("complex_method", {"ccn": 75, "nloc": 251, "max_nesting": 5, "dispatch_pct": 93}, "dispatch"),
        ("nested_complexity", {"ccn": 35, "nloc": 37, "max_nesting": 34}, "chain"),
        # Deep in one block of a short function.
        ("nested_complexity", {"ccn": 13, "nloc": 35, "max_nesting": 6}, "deep_block"),
        # Long, with simple control flow.
        ("large_method", {"ccn": 18, "nloc": 212, "max_nesting": 3}, "straight"),
        # Extreme size is worth doing whatever the branching looks like.
        ("complex_method", {"ccn": 530, "nloc": 2564, "max_nesting": 9, "dispatch_pct": 90}, None),
        # Length alone stays lower priority however long it is.
        ("large_method", {"ccn": 13, "nloc": 809, "max_nesting": 3}, "straight"),
        ("large_method", {"ccn": 14, "nloc": 60, "max_nesting": 3}, "near_bar"),
        # Nesting 8 counts at any length; under it, only past 100 lines.
        ("nested_complexity", {"ccn": 12, "nloc": 34, "max_nesting": 8}, None),
        ("nested_complexity", {"ccn": 12, "nloc": 99, "max_nesting": 6}, "deep_block"),
        ("nested_complexity", {"ccn": 12, "nloc": 100, "max_nesting": 6}, None),
        ("nested_complexity", {"ccn": 12, "nloc": 150, "max_nesting": 5}, "near_bar"),
        # The size bars, either side.
        ("complex_method", {"ccn": 39, "nloc": 120, "max_nesting": 4}, "near_bar"),
        ("complex_method", {"ccn": 40, "nloc": 120, "max_nesting": 4}, None),
        ("large_method", {"ccn": 22, "nloc": 199, "max_nesting": 4}, "near_bar"),
        ("large_method", {"ccn": 22, "nloc": 200, "max_nesting": 4}, None),
        # Branchy and deep under the bars, either side.
        ("nested_complexity", {"ccn": 25, "nloc": 73, "max_nesting": 5}, None),
        ("nested_complexity", {"ccn": 24, "nloc": 73, "max_nesting": 5}, "near_bar"),
        ("complex_method", {"ccn": 30, "nloc": 90, "max_nesting": 4}, "near_bar"),
        # Local fixes.
        ("complex_conditional", {"ccn": 32, "nloc": 75}, "condition"),
        ("error_handling", {}, "handler"),
        # Other kinds keep their own rules.
        ("split_file", {"ccn": 3}, None),
        ("god_class", {}, "design"),
        ("low_cohesion", {"lcom4": 4}, "design"),
        # Nothing measured is no ground to call a function small.
        ("complex_method", {}, None),
    ],
)
def test_low_priority_reads_code_shape(marker, shape, expected) -> None:
    assert low_priority(marker, shape) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("swallowed_catch", "swallow"),
        ("go_swallow", "swallow"),
        ("broad_except", "broad_catch"),
        ("unsafe_unwrap", "unwrap"),
        ("panic_macro", "unwrap"),
        (None, "handler"),
    ],
)
def test_an_error_site_is_labelled_by_what_it_does(kind, expected) -> None:
    assert low_priority("error_handling", {}, error_kind=kind) == expected


def test_an_extract_method_plan_is_judged_by_its_function() -> None:
    small = {"ccn": 10, "nloc": 40}
    assert low_priority("split_file", small) is None
    assert low_priority(None, small, function_size=True) == "near_bar"
    assert low_priority("complex_conditional", small, function_size=True) == "condition"


def test_every_reason_has_a_label_that_does_not_ask_for_action() -> None:
    for reason in get_args(LowPriority):
        assert LOW_PRIORITY_LABEL[reason].startswith("lower priority: ")


def _finding(path: str, name: str, details: dict, impact: float, marker="complex_method"):
    return {
        "file_path": path,
        "biomarker_type": marker,
        "severity": "high",
        "function_name": name,
        "line_start": 10,
        "line_end": 200,
        "health_impact": impact,
        "public_id": f"f_{name}",
        "dimension": "defect",
        "details": {**details, "deepest_block": {"start": 20, "end": 30}},
    }


def test_history_orders_within_a_tier_but_never_lifts_a_small_function() -> None:
    metrics = [
        # The near-bar function sits in the busiest, most imported file.
        {"file_path": "src/hot.py", "nloc": 90, "is_test": False, "commit_count_90d": 40,
         "dependents": 30, "code_origin": "production"},
        {"file_path": "src/big.py", "nloc": 400, "is_test": False, "commit_count_90d": 1,
         "dependents": 1, "code_origin": "production"},
        *[
            {"file_path": f"src/quiet{i}.py", "nloc": 10, "is_test": False,
             "commit_count_90d": 0, "dependents": 0, "code_origin": "production"}
            for i in range(8)
        ],
    ]
    findings = [
        _finding("src/hot.py", "tidy", {"ccn": 14, "nloc": 40, "max_nesting": 4}, 3.0),
        _finding("src/big.py", "sprawl", {"ccn": 52, "nloc": 240, "max_nesting": 5}, 0.4),
    ]
    queue = build_fix_first(metrics=metrics, findings=findings)
    assert [(i.target.symbol, i.tier) for i in queue.items] == [
        ("sprawl", "next"),
        ("tidy", "later"),
    ]
    tidy = queue.items[1]
    assert ("tier", LOW_PRIORITY_LABEL["near_bar"]) in [
        (f.factor, f.value) for f in tidy.why_ranked
    ]
    # Still listed, still true: the item names the finding it came from, and
    # no rank fact or severity reads as top-tier work beside the tier reason.
    assert tidy.title == "Reduce the branching in tidy"
    ranked = {f.factor: f.value for f in tidy.why_ranked}
    assert "value" not in ranked and "value within later" in ranked
    assert ranked["problem size"] == "0"
    assert ("severity", "high by the detector; lower priority by shape") in [
        (f.label, f.value) for f in tidy.facts
    ]


def test_a_cloned_dispatcher_stays_listed_but_waits() -> None:
    """A dispatcher with a duplicate inside is no longer excluded, so it is a
    candidate; the shape rule keeps it out of the top tier."""
    metrics = [{"file_path": "src/p.py", "nloc": 500, "is_test": False,
                "code_origin": "production"}]
    findings = [_finding("src/p.py", "parse", {"ccn": 75, "nloc": 251, "max_nesting": 5,
                                                "dispatch_share": 0.93}, 1.6)]
    plans = [{"public_id": "h1", "refactoring_type": "extract_helper",
              "plan": {"occurrences": [{"file": "src/p.py", "line_start": 40, "line_end": 60}]}}]
    (item,) = build_fix_first(metrics=metrics, findings=findings, plans=plans).items
    assert item.tier == "later"
    assert ("tier", LOW_PRIORITY_LABEL["dispatch"]) in [(f.factor, f.value) for f in item.why_ranked]


def test_findings_are_tiered_per_function_and_history_waits() -> None:
    from repowise.core.analysis.health.worth import finding_priorities

    big = _finding("src/a.py", "big", {"ccn": 47, "nloc": 191, "max_nesting": 5}, 0.4)
    # Two findings on one function: the nesting finding alone says nothing
    # about size, the function's other finding does.
    deep = _finding("src/a.py", "big", {"max_nesting": 5}, 0.9, marker="nested_complexity")
    small = _finding("src/b.py", "small", {"ccn": 12, "nloc": 40}, 1.5)
    churn = {"file_path": "src/c.py", "biomarker_type": "change_entropy",
             "severity": "high", "health_impact": 2.5, "dimension": "defect"}
    rows = [churn, small, deep, big]
    assert finding_priorities(rows) == ["history", "near_bar", None, None]


@pytest.mark.parametrize(
    ("context", "facets", "expected"),
    [
        ("production", {"loop_magnitude": "grows_with_data"}, None),
        ("production", {"loop_magnitude": "bounded"}, "bounded_loop"),
        ("production", {"loop_magnitude": "unknown"}, "unmeasured_cost"),
        ("production", {}, "unmeasured_cost"),
        ("tooling", {"loop_magnitude": "grows_with_data"}, "not_production"),
        ("unknown", {"loop_magnitude": "grows_with_data"}, "unknown_context"),
    ],
)
def test_a_perf_cause_leads_only_on_a_loop_that_grows(context, facets, expected) -> None:
    from repowise.core.analysis.health.queue.value import perf_low_priority

    row = {"execution_context": context, "biomarker_type": "io_in_loop",
           "details": {"facets": facets}}
    assert perf_low_priority(row) == expected


@pytest.mark.parametrize(
    ("marker", "facets", "expected"),
    [
        ("hot_path_sync_io", {"exposure": "entry_reachable"}, None),
        ("hot_path_sync_io", {"exposure": "not_entry_reachable"}, "unreached_call"),
        ("blocking_sync_in_async", {}, "unreached_call"),
        ("goroutine_in_unbounded_loop", {}, None),
        ("sql_cartesian_join", {"loop_magnitude": "unknown"}, None),
        ("unbounded_read_reduced_in_memory", {"exposure": "entry_reachable"}, None),
        ("unbounded_read_reduced_in_memory", {"loop_magnitude": "n/a"}, "unreached_call"),
        # A marker with no loop is judged per call, never as an unmeasured loop.
        ("some_new_marker", {"loop_magnitude": "n/a", "exposure": "entry_reachable"}, None),
        ("some_new_marker", {"loop_magnitude": "n/a"}, "unreached_call"),
    ],
)
def test_a_perf_cause_is_judged_by_its_kind(marker, facets, expected) -> None:
    from repowise.core.analysis.health.queue.value import perf_low_priority

    row = {"execution_context": "production", "biomarker_type": marker,
           "details": {"facets": facets}}
    assert perf_low_priority(row) == expected


@pytest.mark.parametrize(
    ("marker", "facets", "proof"),
    [
        ("io_in_loop", {"loop_magnitude": "unknown"}, "unproven"),
        ("io_in_loop", {}, "unproven"),
        ("io_in_loop", {"loop_magnitude": "bounded"}, "proven"),
        ("io_in_loop", {"loop_magnitude": "grows_with_data"}, "proven"),
        ("hot_path_sync_io", {"exposure": "not_entry_reachable"}, "proven"),
        ("sql_cartesian_join", {}, "proven"),
        ("unbounded_read_reduced_in_memory", {"loop_magnitude": "n/a"}, "proven"),
        ("some_new_marker", {"loop_magnitude": "n/a"}, "proven"),
    ],
)
def test_only_an_unmeasured_loop_is_unproven(marker, facets, proof) -> None:
    from repowise.core.analysis.health.queue.eligibility import perf_queue_verdict
    from repowise.core.analysis.health.worth import cost_proof

    row = {"execution_context": "production", "actionability_state": "advisory",
           "biomarker_type": marker, "details": {"facets": facets}}
    assert cost_proof(row) == proof
    # The default queue leaves an unproven cause out under the shared reason.
    assert perf_queue_verdict(row).reason == ("unmeasured_cost" if proof == "unproven" else None)


def test_an_unknown_loop_ranks_no_higher_than_a_bounded_one() -> None:
    from repowise.core.analysis.health.perf.opportunity_rank import MAGNITUDE_POINTS

    assert MAGNITUDE_POINTS["unknown"] <= MAGNITUDE_POINTS["bounded"]
    assert MAGNITUDE_POINTS["unknown"] < MAGNITUDE_POINTS["grows_with_data"]


def test_the_default_queue_counts_an_unproven_cause_and_still_adds_up() -> None:
    from repowise.core.analysis.health.queue.eligibility import perf_queue_counts

    def row(context: str, magnitude: str) -> dict:
        return {"execution_context": context, "actionability_state": "advisory",
                "biomarker_type": "io_in_loop",
                "details": {"facets": {"loop_magnitude": magnitude}}}

    rows = [row("production", "grows_with_data"), row("production", "unknown"),
            row("test", "unknown")]
    counts = perf_queue_counts(rows)
    assert counts["total"] == 1
    assert counts["excluded"]["unmeasured_cost"] == 1 and counts["excluded"]["test"] == 1
    assert counts["total"] + sum(counts["excluded"].values()) == len(rows)
