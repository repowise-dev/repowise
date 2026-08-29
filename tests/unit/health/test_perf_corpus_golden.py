"""Golden characterization of the performance opportunity read model.

Asserts the exact serialized output of the engine over a checked-in corpus, so
a change to grouping, actionability, or ranking shows up as a reviewable golden
diff rather than a silent count move. Nothing here claims the current grouping
is correct; several cases pin behaviour that should improve.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.perf.opportunities import (
    build_performance_opportunities,
    link_performance_findings,
)
from repowise.core.analysis.health.perf.opportunity_rank import ACTIONABILITY_ORDER
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)

from .perf_corpus_fixture import (
    load_golden,
    observation_rows,
    rewrite_requested,
    rows_for,
    write_golden,
)

EVIDENCE_LIMIT = 8


def _opportunities():
    return build_performance_opportunities(observation_rows(), evidence_limit=EVIDENCE_LIMIT)


def _plan_payloads(opportunities):
    return [
        {
            "refactoring_type": plan.refactoring_type,
            "file_path": plan.file_path,
            "target_symbol": plan.target_symbol,
            "line_start": plan.line_start,
            "line_end": plan.line_end,
            "plan": plan.plan,
            "evidence": plan.evidence,
            "impact_delta": plan.impact_delta,
            "blast_radius": plan.blast_radius,
            "confidence": plan.confidence,
            "source_biomarker": plan.source_biomarker,
        }
        for plan in performance_fix_suggestions(opportunities)
    ]


def test_opportunity_payloads_and_order_match_the_golden() -> None:
    """Identity, membership, ordering, confidence, and evidence in one artifact."""
    payload = [item.as_dict() for item in _opportunities()]
    if rewrite_requested():
        write_golden("golden_opportunities.json", {"opportunities": payload})
        pytest.skip("golden rewritten")
    assert payload == load_golden("golden_opportunities.json")["opportunities"]


def test_performance_plan_payloads_match_the_golden() -> None:
    """Exact plan linkage: every plan names the opportunity id it addresses."""
    payload = _plan_payloads(_opportunities())
    if rewrite_requested():
        write_golden("golden_plans.json", {"plans": payload})
        pytest.skip("golden rewritten")
    assert payload == load_golden("golden_plans.json")["plans"]


def test_every_plan_names_an_opportunity_that_the_corpus_still_produces() -> None:
    """The only accepted plan join is exact ``opportunity_id`` equality."""
    opportunities = _opportunities()
    ids = {item.opportunity_id for item in opportunities}
    plans = performance_fix_suggestions(opportunities)
    assert plans, "the corpus must exercise the plan path"
    assert {plan.plan["opportunity_id"] for plan in plans} <= ids


def test_grouping_is_independent_of_input_order() -> None:
    rows = observation_rows()
    forward = [item.as_dict() for item in build_performance_opportunities(rows)]
    reverse = [item.as_dict() for item in build_performance_opportunities(list(reversed(rows)))]
    assert forward == reverse


def test_a_subset_of_the_corpus_reproduces_the_same_identities() -> None:
    """Opportunity identity does not depend on which other rows were analyzed.

    This is the invariant the incremental path relies on: a partial run sees a
    subset of observations and must still stamp the ids a full run would.
    """
    whole = {item.opportunity_id: item.execution_context for item in _opportunities()}
    for case in (
        "generic_infra_sink",
        "specific_shared_helper",
        "shared_dominator_fan_in",
        "context_split",
    ):
        subset = build_performance_opportunities(rows_for(case), evidence_limit=EVIDENCE_LIMIT)
        assert subset, case
        for item in subset:
            assert item.opportunity_id in whole, case
            assert whole[item.opportunity_id] == item.execution_context, case


def test_linking_stamps_the_id_the_builder_derives() -> None:
    rows = observation_rows()
    link_performance_findings(rows)
    opportunities = _opportunities()
    by_id = {item.opportunity_id: item for item in opportunities}
    stamped = {row["details"]["opportunity_id"] for row in rows}
    assert stamped == set(by_id)
    for item in opportunities:
        members = [row for row in rows if row["details"]["opportunity_id"] == item.opportunity_id]
        assert len(members) == item.observations_total


@pytest.mark.parametrize(
    ("case", "expected_groups", "expected_strategies"),
    [
        # Three unrelated callers reach one generic session opener. They are
        # three causes, and each one carries the plan the merged group could
        # not: plan availability no longer falls as caller count rises.
        ("generic_infra_sink", 3, {"batch_or_prefetch_io"}),
        # A specific shared helper is one cause however many callers reach it.
        ("specific_shared_helper", 1, {"batch_or_prefetch_io"}),
        ("shared_dominator_fan_in", 1, {None}),
        # Filesystem repetition is real but has no batch API to point at.
        ("filesystem_fan_out", 2, {None}),
        ("same_file_db_helper", 1, {"batch_or_prefetch_io"}),
        ("async_serial_awaits", 2, {"parallelize_independent_awaits", None}),
        ("single_lock_owner", 1, {"shrink_lock_scope"}),
        # One helper, two lock owners behind it: still one cause, still no
        # single critical section to shorten.
        ("distinct_lock_owners", 1, {None}),
        ("membership_scan", 1, {"replace_membership_collection"}),
        ("string_accumulation", 1, {"buffer_string_accumulation"}),
        ("resource_construction", 2, {"hoist_loop_invariant_resource", None}),
        # Execution context is an identity input: one shape, three contexts.
        ("context_split", 3, {"batch_or_prefetch_io"}),
        # io_in_loop and nested_loop_with_io share one cross-function identity
        # when they share a caller, and merging no longer costs the plan.
        ("cost_shape_merge", 1, {"batch_or_prefetch_io"}),
        ("provenance_mix", 2, {"batch_or_prefetch_io"}),
        ("reachability_states", 1, {"batch_or_prefetch_io"}),
        ("unclassified_context", 2, {None}),
        ("sink_without_caller", 1, {"batch_or_prefetch_io"}),
    ],
)
def test_case_membership_and_actionability(case, expected_groups, expected_strategies) -> None:
    opportunities = build_performance_opportunities(rows_for(case), evidence_limit=EVIDENCE_LIMIT)
    assert len(opportunities) == expected_groups
    assert {
        item.fix.strategy if item.fix else None for item in opportunities
    } == expected_strategies


def test_a_generic_sink_splits_by_caller_and_a_shared_helper_does_not() -> None:
    """The two poles of the grouping rule, asserted against each other.

    Both cases put several callers on one sink. The difference is whether a
    helper stands between them, and that is the whole of what decides a shared
    cause from a shared destination.
    """
    generic = build_performance_opportunities(rows_for("generic_infra_sink"))
    shared = build_performance_opportunities(rows_for("shared_dominator_fan_in"))
    assert len(generic) == 3
    assert {item.observations_total for item in generic} == {1}
    assert len(shared) == 1
    assert shared[0].observations_total == 4
    assert shared[0].intervention_symbol == "src/app/parse.py::_get_query"
    assert shared[0].facets["leverage"] == "shared"


def test_an_unclassifiable_path_is_not_reported_as_production() -> None:
    contexts = {
        item.execution_context
        for item in build_performance_opportunities(rows_for("unclassified_context"))
    }
    assert contexts == {"unknown"}


def test_a_path_that_names_no_caller_is_keyed_locally() -> None:
    """A single-node path is a destination with no journey to it."""
    item = build_performance_opportunities(rows_for("sink_without_caller"))[0]
    assert item.intervention_symbol is None
    assert item.terminal_sink is None


def test_every_group_reports_an_actionability_state_and_a_reason() -> None:
    """Nothing is dropped for being unexplainable; it is labelled instead."""
    states = {"plan_ready", "advisory", "investigate"}
    for item in _opportunities():
        assert item.actionability_state in states
        assert item.actionability_reason
        if item.actionability_state == "investigate":
            assert item.fix is None
            assert item.prerequisites, item.opportunity_id


def test_actionable_groups_sort_above_higher_scoring_evidence() -> None:
    """Raw magnitude must not bury work somebody could start today."""
    order = [item.actionability_state for item in _opportunities()]
    assert order == sorted(order, key=ACTIONABILITY_ORDER.__getitem__)


def test_rank_rationale_is_bounded_and_never_pads_with_nothing() -> None:
    for item in _opportunities():
        assert len(item.why_ranked) <= 3
        assert all(entry["points"] > 0 for entry in item.why_ranked)


def test_evidence_confidence_and_actionability_move_independently() -> None:
    """The two are separate readings of one group and must be able to disagree.

    ``provenance_mix`` holds the sharp pair: the same strategy on the same
    boundary, demoted only because the call path behind it was resolved less
    reliably.
    """
    by_provenance = {
        item.provenance: item
        for item in build_performance_opportunities(rows_for("provenance_mix"))
    }
    reliable = by_provenance["reliable-edge"]
    guessed = by_provenance["name-fallback"]
    assert reliable.fix and guessed.fix
    assert reliable.fix.strategy == guessed.fix.strategy
    assert reliable.confidence == "medium"
    assert guessed.confidence == "low"
    assert reliable.facets["actionability_confidence"] == "medium"
    assert guessed.facets["actionability_confidence"] == "low"
    assert reliable.actionability_reason == "strategy_requires_validation"
    assert guessed.actionability_reason == "low_evidence_confidence"
    assert "reliable_call_path" in guessed.prerequisites


def test_reachability_aggregates_any_true_over_all_false() -> None:
    item = build_performance_opportunities(rows_for("reachability_states"))[0]
    assert item.reliable_entry_reachability is True
    assert item.observations_total == 2
