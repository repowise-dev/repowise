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
    first = _finding("a.py", 2, call_path=("a.py::run", "db.py::fetch"))
    original = build_performance_opportunities([first])[0]
    added = _finding(
        "b.py",
        3,
        marker="nested_loop_with_io",
        call_path=("b.py::run", "db.py::fetch"),
    )
    expanded = build_performance_opportunities([added, first])[0]
    assert expanded.opportunity_id == original.opportunity_id
    assert expanded.biomarker_types == ("io_in_loop", "nested_loop_with_io")
    assert expanded.observations_total == 2


def test_capped_evidence_retains_true_totals():
    rows = [
        _finding(f"caller_{index}.py", index, call_path=(f"caller_{index}.py::run", "db.py::fetch"))
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


def test_generic_sink_without_shared_caller_stays_advisory_without_a_plan():
    opportunity = build_performance_opportunities(
        [
            _finding("a.py", 2, call_path=("a.py::run", "db.py::get_session")),
            _finding("b.py", 3, call_path=("b.py::run", "db.py::get_session")),
        ]
    )[0]

    assert opportunity.shared_path_suffix == ("db.py::get_session",)
    assert opportunity.fix is None


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


def test_distinct_lock_owners_sharing_a_sink_do_not_claim_one_lock_fix():
    opportunity = build_performance_opportunities(
        [
            _finding(
                "a.py",
                3,
                marker="blocking_io_under_lock",
                call_path=("a.py::critical", "db.py::fetch"),
            ),
            _finding(
                "b.py",
                4,
                marker="blocking_io_under_lock",
                call_path=("b.py::critical", "db.py::fetch"),
            ),
        ]
    )[0]

    assert opportunity.fix is None


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
