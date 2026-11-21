"""A function a leading run measured above the lead bar becomes work, once."""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.actionability import MEASURED_HOTSPOT
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.queue.eligibility import (
    perf_confident_verdict,
    perf_queue_verdict,
)
from repowise.core.analysis.health.refactoring.performance_fix import (
    performance_fix_suggestions,
)

HOT = "pkg/coverage_map.py::_decode_lines"
METHOD = "pkg/render.py::PageGenerator::_render"
OWNER = "app/api/orders.py::list_orders"


def _finding() -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="app/api/orders.py",
        function_name="list_orders",
        line_start=10,
        line_end=10,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [OWNER, "app/repo.py::load_items"],
            "role_owner": OWNER,
            "loop_magnitude": "grows_with_data",
        },
        health_impact=0.0,
        dimension="performance",
    )


def test_a_measured_function_with_no_static_cause_is_confident_work() -> None:
    (item,) = build_performance_opportunities(
        [], measured={HOT: "update"}, hot=frozenset({HOT})
    )

    assert item.biomarker_type == MEASURED_HOTSPOT
    assert item.execution_context == "production"
    assert item.intervention_symbol == HOT
    assert item.facets["confirmed"] == "measured"
    assert item.facets["measured"] == "update"
    assert item.may_lead
    assert perf_queue_verdict(item).eligible
    assert perf_confident_verdict(item).eligible
    # The plan is the reader's call: one judgment step at the function.
    (plan,) = performance_fix_suggestions([item])
    (step,) = plan.plan["steps"]
    assert step["applicability"] == "judgment"
    assert step["symbol"] == HOT
    assert "_decode_lines" in step["action"]


def test_a_method_keeps_its_class_in_the_symbol() -> None:
    (item,) = build_performance_opportunities(
        [], measured={METHOD: "update"}, hot=frozenset({METHOD})
    )

    assert item.intervention_symbol == METHOD
    assert item.evidence[0]["file_path"] == "pkg/render.py"


def test_a_static_cause_that_owns_the_function_is_promoted_never_doubled() -> None:
    items = build_performance_opportunities(
        [_finding()], measured={OWNER: "api"}, hot=frozenset({OWNER})
    )

    assert [i.biomarker_type for i in items] == ["io_in_loop"]
    assert items[0].facets["confirmed"] == "measured"


def test_only_a_leading_run_above_the_bar_mints_one() -> None:
    # Ranked below the bar: measured, not hot.
    assert build_performance_opportunities([], measured={HOT: "update"}) == []
    # A unit-test run above the bar: never.
    assert (
        build_performance_opportunities(
            [], in_tests={HOT: "pytest"}, hot_in_tests=frozenset({HOT})
        )
        == []
    )


def test_hotspots_sort_with_the_confirmed_causes() -> None:
    items = build_performance_opportunities(
        [_finding()], measured={HOT: "update"}, hot=frozenset({HOT})
    )

    assert [i.biomarker_type for i in items] == [MEASURED_HOTSPOT, "io_in_loop"]


def test_hotspots_follow_the_place_their_run_measured() -> None:
    first, second = "pkg/a.py::zz_top", "pkg/b.py::aa_second"
    items = build_performance_opportunities(
        [],
        measured={first: "update", second: "update"},
        hot=frozenset({first, second}),
        ranks={first: 1, second: 2},
    )

    assert [i.intervention_symbol for i in items] == [first, second]
    assert items[0].rank_factors["measured_rank"] == 3
