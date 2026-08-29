from __future__ import annotations

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import (
    build_performance_opportunities,
    link_performance_findings,
)
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)


def _finding(
    path: str,
    line: int,
    *,
    marker: str = "io_in_loop",
    boundary: str = "db",
    call_path: tuple[str, ...] = (),
    **details: object,
) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": boundary,
            "cross_function": bool(call_path),
            "path": list(call_path),
            **details,
        },
        health_impact=0.0,
        dimension="performance",
    )


def test_shared_sink_groups_once_links_raw_rows_and_selects_lowest_shared_intervention():
    rows = [
        _finding("a.py", 10, call_path=("a.py::run", "shared.py::load", "git.py::head")),
        _finding("b.py", 20, call_path=("b.py::run", "shared.py::load", "git.py::head")),
    ]

    link_performance_findings(rows)
    opportunities = build_performance_opportunities(rows)

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.observations_total == 2
    assert opportunity.affected_call_sites_total == 2
    assert opportunity.affected_files_total == 2
    assert opportunity.shared_path_suffix == ("shared.py::load", "git.py::head")
    assert opportunity.intervention_symbol == "shared.py::load"
    assert {row.details["opportunity_id"] for row in rows} == {opportunity.opportunity_id}


def test_grouping_and_order_are_input_order_independent():
    rows = [
        _finding("z.py", 4, marker="string_concat_in_loop", boundary=""),
        _finding("a.py", 2, call_path=("a.py::run", "db.py::fetch")),
        _finding("b.py", 3, call_path=("b.py::run", "db.py::fetch")),
    ]
    forward = build_performance_opportunities(rows)
    reverse = build_performance_opportunities(list(reversed(rows)))
    assert [item.as_dict() for item in forward] == [item.as_dict() for item in reverse]


def test_cross_function_id_survives_new_caller_and_stronger_multiplier_observation():
    first = _finding("a.py", 2, call_path=("a.py::run", "shared.py::load", "db.py::fetch"))
    original = build_performance_opportunities([first])[0]
    added = _finding(
        "b.py",
        3,
        marker="nested_loop_with_io",
        call_path=("b.py::run", "shared.py::load", "db.py::fetch"),
    )
    expanded = build_performance_opportunities([added, first])[0]
    assert expanded.opportunity_id == original.opportunity_id
    assert expanded.biomarker_types == ("io_in_loop", "nested_loop_with_io")
    assert expanded.observations_total == 2


def test_capped_evidence_retains_true_totals():
    rows = [
        _finding(
            f"caller_{index}.py",
            index,
            call_path=(f"caller_{index}.py::run", "shared.py::load", "db.py::fetch"),
        )
        for index in range(1, 7)
    ]
    opportunity = build_performance_opportunities(rows, evidence_limit=2)[0]
    assert len(opportunity.evidence) == 2
    assert opportunity.evidence_truncated is True
    assert opportunity.observations_total == 6
    assert opportunity.affected_call_sites_total == 6
    assert opportunity.affected_files_total == 6


def test_strategy_preconditions_distinguish_proven_advisory_and_no_plan():
    verified = build_performance_opportunities(
        [
            _finding(
                "async.py",
                5,
                marker="serial_await_in_loop",
                boundary="network",
                dataflow_verified=True,
            )
        ]
    )[0]
    ambiguous = build_performance_opportunities(
        [_finding("async.py", 6, marker="serial_await_in_loop", boundary="network")]
    )[0]
    batching = build_performance_opportunities(
        [_finding("db.py", 7, call_path=("db.py::run", "db.py::fetch"))]
    )[0]
    filesystem = build_performance_opportunities(
        [
            _finding(
                "fs.py",
                8,
                boundary="filesystem",
                call_path=("fs.py::run", "fs.py::read"),
            )
        ]
    )[0]

    assert verified.fix and verified.fix.strategy == "parallelize_independent_awaits"
    assert verified.fix.safety == "proven"
    assert ambiguous.fix is None
    assert batching.fix and batching.fix.strategy == "batch_or_prefetch_io"
    assert batching.fix.safety == "advisory"
    assert filesystem.fix is None

    membership = build_performance_opportunities(
        [_finding("lookup.py", 9, marker="membership_test_against_list_in_loop", boundary="")]
    )[0]
    concat = build_performance_opportunities(
        [_finding("text.py", 10, marker="string_concat_in_loop", boundary="")]
    )[0]
    assert membership.fix and membership.fix.safety == "advisory"
    assert concat.fix and concat.fix.safety == "advisory"


def test_a_generic_sink_splits_by_caller_so_each_workflow_keeps_its_plan():
    """Two unrelated callers on one session opener are two causes.

    Merging them named an intervention nobody could make, and the merged group
    then failed the coherence check and lost its plan, so the more callers a
    sink had the less actionable it became. Each caller now keeps its own.
    """
    opportunities = build_performance_opportunities(
        [
            _finding("a.py", 2, call_path=("a.py::run", "db.py::get_session")),
            _finding("b.py", 3, call_path=("b.py::run", "db.py::get_session")),
        ]
    )

    assert len(opportunities) == 2
    assert {item.intervention_symbol for item in opportunities} == {"a.py::run", "b.py::run"}
    assert all(item.fix and item.fix.strategy == "batch_or_prefetch_io" for item in opportunities)
    assert all(item.actionability_state == "advisory" for item in opportunities)


def test_incompatible_cross_function_cost_shapes_never_share_a_fix():
    rows = [
        _finding("loop.py", 2, call_path=("loop.py::run", "db.py::fetch")),
        _finding(
            "lock.py",
            3,
            marker="blocking_io_under_lock",
            call_path=("lock.py::critical", "db.py::fetch"),
        ),
    ]

    opportunities = build_performance_opportunities(rows)
    assert len(opportunities) == 2
    assert {item.biomarker_type for item in opportunities} == {
        "io_in_loop",
        "blocking_io_under_lock",
    }


def test_lock_fix_targets_the_single_lock_owner_not_the_shared_sink():
    opportunity = build_performance_opportunities(
        [
            _finding(
                "lock.py",
                3,
                marker="blocking_io_under_lock",
                call_path=("lock.py::critical", "db.py::fetch"),
            )
        ]
    )[0]
    plan = performance_fix_suggestions([opportunity])[0]

    assert plan.plan["strategy"] == "shrink_lock_scope"
    assert plan.target_symbol == "lock.py::critical"


def test_distinct_lock_owners_behind_one_helper_do_not_claim_one_lock_fix():
    """One shared helper, two critical sections: no single scope to shorten."""
    opportunity = build_performance_opportunities(
        [
            _finding(
                "a.py",
                3,
                marker="blocking_io_under_lock",
                call_path=("a.py::critical", "store.py::flush", "db.py::fetch"),
            ),
            _finding(
                "b.py",
                4,
                marker="blocking_io_under_lock",
                call_path=("b.py::critical", "store.py::flush", "db.py::fetch"),
            ),
        ]
    )[0]

    assert opportunity.observations_total == 2
    assert opportunity.fix is None
    assert opportunity.actionability_state == "investigate"
    assert opportunity.prerequisites == ("single_lock_owner",)


def test_performance_fix_plan_carries_closed_strategy_and_true_totals():
    opportunity = build_performance_opportunities(
        [
            _finding("a.py", 10, call_path=("a.py::run", "shared.py::load", "db.py::fetch")),
            _finding("b.py", 20, call_path=("b.py::run", "shared.py::load", "db.py::fetch")),
        ],
        evidence_limit=1,
    )[0]
    plans = performance_fix_suggestions([opportunity], nloc_by_file={"shared.py": 80})
    assert len(plans) == 1
    plan = plans[0]
    assert plan.refactoring_type == "performance_fix"
    assert plan.file_path == "shared.py"
    assert plan.target_symbol == "shared.py::load"
    assert plan.plan["strategy"] == "batch_or_prefetch_io"
    assert plan.plan["safety"] == "advisory"
    assert plan.plan["affected_locations_total"] == 2
    assert plan.plan["paths_total"] == 2
    assert plan.plan["evidence_truncated"] is True
    assert performance_fix_suggestions([opportunity], min_confidence="high") == []


def test_a_proven_strategy_on_an_unreliable_path_is_not_offered_as_proven():
    """The demotion has to reach the plan, not just the label.

    Dataflow can prove a loop carries no cross-iteration dependence and still
    be proving it about the wrong loop, if the call path that grouped the
    evidence was guessed. A plan stamped high confidence on that basis is the
    wrong plan, which costs more than no plan at all.

    No detector emits this shape today: the two markers carrying a proven
    strategy never travel with a call path, so their provenance is always
    direct. The guard is pinned here rather than in the corpus because the
    corpus records shapes the analyzer produces.
    """
    opportunity = build_performance_opportunities(
        [
            _finding(
                "async.py",
                5,
                marker="serial_await_in_loop",
                boundary="network",
                call_path=("async.py::run", "http.py::send", "http.py::_write"),
                dataflow_verified=True,
                resolution_basis="name-fallback",
            )
        ]
    )[0]

    assert opportunity.confidence == "low"
    assert opportunity.actionability_state == "advisory"
    assert opportunity.actionability_reason == "low_evidence_confidence"
    assert "reliable_call_path" in opportunity.prerequisites
    assert opportunity.fix and opportunity.fix.safety == "advisory"
    assert performance_fix_suggestions([opportunity])[0].confidence == "medium"


def test_a_reliable_path_keeps_the_proven_strategy_and_its_plan_confidence():
    opportunity = build_performance_opportunities(
        [
            _finding(
                "async.py",
                5,
                marker="serial_await_in_loop",
                boundary="network",
                dataflow_verified=True,
            )
        ]
    )[0]

    assert opportunity.actionability_state == "plan_ready"
    assert opportunity.fix and opportunity.fix.safety == "proven"
    assert performance_fix_suggestions([opportunity])[0].confidence == "high"


def test_a_cross_function_group_always_shares_its_last_two_path_nodes():
    """The published suffix and the identity kernel must not drift apart.

    Grouping matches on caller and sink, so the suffix can be longer than two
    nodes but never shorter, and its tail is exactly what the key already
    names. A redefinition of either that broke this would silently point plans
    at a symbol the group does not share.
    """
    rows = [
        _finding("a.py", 10, call_path=("a.py::run", "mid.py::step", "shared.py::load", "g.py::head")),
        _finding("b.py", 20, call_path=("b.py::run", "other.py::step", "shared.py::load", "g.py::head")),
    ]
    opportunity = build_performance_opportunities(rows)[0]

    assert opportunity.observations_total == 2
    assert opportunity.shared_path_suffix == ("shared.py::load", "g.py::head")
    assert opportunity.shared_path_suffix[-2:] == (
        opportunity.intervention_symbol,
        opportunity.terminal_sink,
    )
