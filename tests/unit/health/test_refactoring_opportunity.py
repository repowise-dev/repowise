"""Composition, ordering, demotion, identity and lifecycle for opportunities."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.opportunity import (
    STEP_ORDER,
    compose_opportunities,
    is_standalone_clone,
    opportunity_public_id,
    opportunity_status,
)


def plan(kind: str, symbol: str = "handle", **kwargs) -> RefactoringSuggestion:
    payload = {
        "refactoring_type": kind,
        "file_path": "svc/orders.py",
        "target_symbol": symbol,
        "line_start": 10,
        "line_end": 40,
        "plan": {},
        "evidence": {},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }
    payload.update(kwargs)
    return RefactoringSuggestion(**payload)


def clone(*, intra: bool, co_change: int, **kwargs) -> RefactoringSuggestion:
    kwargs.setdefault("blast_radius", {"files": ["svc/billing.py"], "file_count": 1})
    return plan(
        "extract_helper",
        f"orders.py:{co_change}-{int(intra)}",
        evidence={"is_intra_file": intra, "co_change_count": co_change},
        impact_delta=0.0,
        source_biomarker="dry_violation",
        **kwargs,
    )


def split(**kwargs) -> RefactoringSuggestion:
    return plan(
        "split_file",
        "orders.py -> 2 files",
        plan={
            "shim_required": True,
            "groups": [
                {"symbols": ["handle"], "suggested_file": "svc/orders_handle.py"},
                {"symbols": ["render"], "suggested_file": "svc/orders_render.py"},
            ],
        },
        evidence={"group_count": 2},
        impact_delta=0.0,
        blast_radius={"dependent_count": 3, "dependent_files": ["svc/api.py"]},
        source_biomarker="",
        **kwargs,
    )


# --------------------------------------------------------------------------
# membership
# --------------------------------------------------------------------------


def test_one_opportunity_per_file() -> None:
    rows = [plan("extract_method"), plan("extract_method", "render"), split()]
    opportunities = compose_opportunities(rows)
    assert len(opportunities) == 1
    assert opportunities[0].step_count == 3


def test_files_are_not_merged() -> None:
    rows = [plan("extract_method"), plan("extract_method", file_path="svc/billing.py")]
    assert {item.file_path for item in compose_opportunities(rows)} == {
        "svc/orders.py",
        "svc/billing.py",
    }


def test_performance_plans_are_left_to_their_own_layer() -> None:
    rows = [plan("performance_fix", plan={"opportunity_id": "perf2_abc"})]
    assert compose_opportunities(rows) == []


# --------------------------------------------------------------------------
# clone demotion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intra", "co_change", "standalone"),
    [
        (False, 3, True),  # cross-file and co-changed: worth instructing
        (False, 2, False),  # cross-file, but nobody edits them together
        (True, 9, False),  # co-changed, but it is one file's own repetition
        (True, 0, False),
    ],
)
def test_only_cross_file_co_changed_clones_earn_a_step(
    intra: bool, co_change: int, standalone: bool
) -> None:
    assert is_standalone_clone(clone(intra=intra, co_change=co_change)) is standalone


def test_demoted_clones_become_evidence_on_the_file_opportunity() -> None:
    rows = [plan("extract_method"), clone(intra=True, co_change=0)]
    opportunity = compose_opportunities(rows)[0]
    assert opportunity.step_count == 1
    assert [item.refactoring_type for item in opportunity.evidence] == ["extract_helper"]
    assert opportunity.evidence[0].summary["is_intra_file"] is True


def test_a_file_with_only_demoted_clones_publishes_nothing() -> None:
    assert compose_opportunities([clone(intra=True, co_change=0)]) == []


def test_a_high_signal_clone_is_a_step_not_evidence() -> None:
    opportunity = compose_opportunities([clone(intra=False, co_change=7)])[0]
    assert opportunity.step_count == 1
    assert opportunity.evidence == ()


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_structural_steps_precede_extractions() -> None:
    rows = [plan("extract_method"), clone(intra=False, co_change=5), split()]
    steps = compose_opportunities(rows)[0].steps
    assert [step.refactoring_type for step in steps] == [
        "split_file",
        "extract_method",
        "extract_helper",
    ]


def test_step_order_covers_every_composed_type() -> None:
    from repowise.core.analysis.health.refactoring import registered_detectors
    from repowise.core.analysis.health.refactoring.opportunity import EXCLUDED_TYPES

    names = {detector.name for detector in registered_detectors()} - EXCLUDED_TYPES
    assert names <= set(STEP_ORDER), f"unordered detector types: {sorted(names - set(STEP_ORDER))}"


def test_same_type_steps_order_by_position_then_symbol() -> None:
    rows = [
        plan("extract_method", "z", line_start=90),
        plan("extract_method", "a", line_start=10),
    ]
    steps = compose_opportunities(rows)[0].steps
    assert [step.target_symbol for step in steps] == ["a", "z"]


def test_steps_after_a_relocation_say_they_must_be_located_again() -> None:
    steps = compose_opportunities([plan("extract_method"), split()])[0].steps
    assert steps[0].refactoring_type == "split_file"
    assert steps[0].relocated_by is None
    assert steps[1].relocated_by == steps[0].plan_id


def test_no_relocation_leaves_every_step_addressable_as_written() -> None:
    steps = compose_opportunities([plan("extract_method"), plan("extract_method", "x")])[0].steps
    assert all(step.relocated_by is None for step in steps)


# --------------------------------------------------------------------------
# lead diagnosis
# --------------------------------------------------------------------------


def test_the_lead_is_the_files_own_primary_problem() -> None:
    opportunity = compose_opportunities(
        [plan("extract_method")], primary_biomarker_by_file={"svc/orders.py": "complex_method"}
    )[0]
    assert opportunity.lead_biomarker == "complex_method"
    assert opportunity.addresses_primary_problem is True


def test_a_problem_no_detector_answers_is_reported_as_unaddressed() -> None:
    opportunity = compose_opportunities(
        [plan("extract_method")], primary_biomarker_by_file={"svc/orders.py": "churn_risk"}
    )[0]
    assert opportunity.lead_biomarker == "churn_risk"
    assert opportunity.addresses_primary_problem is False


def test_without_the_files_findings_the_question_is_unknown_not_no() -> None:
    opportunity = compose_opportunities([plan("extract_method")])[0]
    assert opportunity.addresses_primary_problem is None
    assert opportunity.rank_factors["primary_problem"] == 0.0


def test_a_purely_structural_file_leads_with_its_structure() -> None:
    opportunity = compose_opportunities([split()])[0]
    assert opportunity.lead_biomarker is None
    assert opportunity.lead_refactoring_type == "split_file"


# --------------------------------------------------------------------------
# rollups
# --------------------------------------------------------------------------


def test_blast_radius_is_charged_once_over_the_union() -> None:
    shared = {"files": ["svc/billing.py"], "file_count": 1}
    one = compose_opportunities([clone(intra=False, co_change=4, blast_radius=shared)])[0]
    two = compose_opportunities(
        [
            clone(intra=False, co_change=4, blast_radius=shared),
            clone(intra=False, co_change=5, blast_radius=shared),
        ]
    )[0]
    assert one.affected_files == two.affected_files == ("svc/billing.py", "svc/orders.py")
    assert one.rank_factors["risk"] == two.rank_factors["risk"]


def test_effort_is_the_largest_step_not_the_sum() -> None:
    rows = [plan("extract_method", effort_bucket="S"), plan("extract_method", "x", effort_bucket="L")]
    assert compose_opportunities(rows)[0].effort_bucket == "L"


def test_confidence_is_the_weakest_step() -> None:
    rows = [plan("extract_method"), plan("extract_method", "x", confidence="medium")]
    assert compose_opportunities(rows)[0].confidence == "medium"


def test_mechanical_and_judgment_steps_are_counted_separately() -> None:
    opportunity = compose_opportunities([plan("extract_method"), split()])[0]
    assert (opportunity.mechanical_steps, opportunity.judgment_steps) == (1, 1)
    assert opportunity.step_count == 2


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------


def test_a_step_set_with_no_benefit_cannot_outrank_one_that_recovers_health() -> None:
    barren = compose_opportunities([clone(intra=False, co_change=4)])[0]
    real = compose_opportunities([plan("extract_method", file_path="svc/billing.py")])[0]
    assert barren.rank_score == 0.0
    assert real.rank_score > barren.rank_score


def test_addressing_the_primary_problem_ranks_above_not_addressing_it() -> None:
    rows = [plan("extract_method"), plan("extract_method", file_path="svc/billing.py")]
    ranked = compose_opportunities(
        rows,
        primary_biomarker_by_file={
            "svc/orders.py": "complex_method",
            "svc/billing.py": "churn_risk",
        },
    )
    assert [item.file_path for item in ranked] == ["svc/orders.py", "svc/billing.py"]


def test_order_is_total_and_repeatable() -> None:
    rows = [plan("extract_method", file_path=f"svc/m{index}.py") for index in range(6)]
    first = [item.opportunity_id for item in compose_opportunities(rows)]
    assert first == [item.opportunity_id for item in compose_opportunities(list(reversed(rows)))]


def test_why_ranked_names_the_factors_that_moved_it() -> None:
    opportunity = compose_opportunities([plan("extract_method")])[0]
    named = {entry["factor"] for entry in opportunity.why_ranked}
    assert named <= set(opportunity.rank_factors)
    assert "benefit" in named


# --------------------------------------------------------------------------
# identity and lifecycle
# --------------------------------------------------------------------------


def test_the_id_is_built_over_the_member_plan_ids() -> None:
    opportunity = compose_opportunities([plan("extract_method"), split()])[0]
    assert opportunity.opportunity_id == opportunity_public_id(
        [step.plan_id for step in opportunity.steps], "svc/orders.py"
    )
    assert opportunity.opportunity_id.startswith("refop2_")


def test_the_id_survives_a_uniform_line_shift() -> None:
    before = compose_opportunities([plan("extract_method")])[0]
    after = compose_opportunities([plan("extract_method", line_start=210, line_end=240)])[0]
    assert before.opportunity_id == after.opportunity_id


def test_the_id_changes_when_the_work_changes() -> None:
    one = compose_opportunities([plan("extract_method")])[0]
    two = compose_opportunities([plan("extract_method"), split()])[0]
    assert one.opportunity_id != two.opportunity_id


def test_evidence_does_not_rename_the_work() -> None:
    bare = compose_opportunities([plan("extract_method")])[0]
    witnessed = compose_opportunities([plan("extract_method"), clone(intra=True, co_change=0)])[0]
    assert bare.opportunity_id == witnessed.opportunity_id
    assert len(witnessed.evidence) == 1


def test_lifecycle_resolves_only_when_every_step_does() -> None:
    opportunity = compose_opportunities([plan("extract_method"), split()])[0]
    first, second = (step.plan_id for step in opportunity.steps)
    assert opportunity_status(opportunity, {}) == "open"
    assert opportunity_status(opportunity, {first: "resolved"}) == "open"
    assert opportunity_status(opportunity, {first: "resolved", second: "resolved"}) == "resolved"
    assert (
        opportunity_status(opportunity, {first: "false_positive", second: "resolved"}) == "resolved"
    )
    assert (
        opportunity_status(opportunity, {first: "acknowledged", second: "resolved"})
        == "acknowledged"
    )


def test_an_untriaged_step_keeps_the_opportunity_open() -> None:
    opportunity = compose_opportunities([plan("extract_method"), split()])[0]
    known = opportunity.steps[0].plan_id
    assert opportunity_status(opportunity, {known: "resolved"}) == "open"


# --------------------------------------------------------------------------
# input shapes
# --------------------------------------------------------------------------


def test_dicts_and_dataclasses_compose_to_the_same_opportunity() -> None:
    from dataclasses import asdict

    rows = [plan("extract_method"), split()]
    from_objects = compose_opportunities(rows)[0]
    from_dicts = compose_opportunities([asdict(row) for row in rows])[0]
    assert from_objects.as_dict() == from_dicts.as_dict()
