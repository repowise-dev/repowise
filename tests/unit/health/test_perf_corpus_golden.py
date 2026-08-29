"""Golden characterization of the performance opportunity read model.

This suite exists to make Phase 2's grouping, actionability, and ranking changes
*visible*. It asserts the exact serialized output of today's engine over a
checked-in corpus, so any later semantic change shows up as a reviewable golden
diff rather than as a silent count move. Nothing here is a claim that current
grouping is correct; several cases pin behaviour the plan intends to change.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.perf.opportunities import (
    build_performance_opportunities,
    link_performance_findings,
)
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
    for case in ("generic_infra_sink", "specific_shared_helper", "context_split"):
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
        # A generic infrastructure sink merges unrelated callers into one group
        # and refuses to claim a plan. Phase 2 is expected to split this.
        ("generic_infra_sink", 1, {None}),
        # A specific shared helper keeps a coherent suffix and does get a plan.
        ("specific_shared_helper", 1, {"batch_or_prefetch_io"}),
        # Filesystem repetition is real but has no batch API to point at.
        ("filesystem_fan_out", 1, {None}),
        ("same_file_db_helper", 1, {"batch_or_prefetch_io"}),
        ("async_serial_awaits", 2, {"parallelize_independent_awaits", None}),
        ("single_lock_owner", 1, {"shrink_lock_scope"}),
        ("distinct_lock_owners", 1, {None}),
        ("membership_scan", 1, {"replace_membership_collection"}),
        ("string_accumulation", 1, {"buffer_string_accumulation"}),
        ("resource_construction", 2, {"hoist_loop_invariant_resource", None}),
        # Execution context is an identity input: one shape, three contexts.
        # Each context holds a single caller, so its shared suffix is the whole
        # path and a plan is offered, while ``generic_infra_sink`` above, with
        # three callers on the same sink, gets none. Plan availability currently
        # falls as caller count rises, which is backwards; Phase 2 owns it.
        ("context_split", 3, {"batch_or_prefetch_io"}),
        # io_in_loop and nested_loop_with_io share one cross-function identity,
        # and two callers shorten the suffix to the sink alone, so no plan.
        ("cost_shape_merge", 1, {None}),
        ("provenance_mix", 2, {"batch_or_prefetch_io"}),
        ("reachability_states", 1, {None}),
    ],
)
def test_case_membership_and_actionability(case, expected_groups, expected_strategies) -> None:
    opportunities = build_performance_opportunities(rows_for(case), evidence_limit=EVIDENCE_LIMIT)
    assert len(opportunities) == expected_groups
    assert {
        item.fix.strategy if item.fix else None for item in opportunities
    } == expected_strategies


def test_confidence_facets_available_today_are_provenance_only() -> None:
    """Evidence confidence and actionability are not yet separable.

    ``confidence`` is derived from call-graph provenance alone; a plan's own
    confidence comes from ``fix.safety`` in the refactoring layer. Splitting
    these facets is Phase 2 work, and this test records the current collapse.
    """
    by_case = {
        "provenance_mix": {"medium", "low"},
        "generic_infra_sink": {"high"},
    }
    for case, expected in by_case.items():
        items = build_performance_opportunities(rows_for(case))
        assert {item.confidence for item in items} == expected


def test_reachability_aggregates_any_true_over_all_false() -> None:
    item = build_performance_opportunities(rows_for("reachability_states"))[0]
    assert item.reliable_entry_reachability is True
    assert item.observations_total == 2
