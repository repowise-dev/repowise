"""Query counts at a database cause's call path reach its facets."""

from __future__ import annotations

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.evidence.query_counts import QueryReading

OWNER = "app/api/orders.py::list_orders"
SINK = "app/repo.py::load_items"


def _finding(boundary: str = "db") -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="app/api/orders.py",
        function_name="list_orders",
        line_start=10,
        line_end=10,
        details={
            "boundary_kind": boundary,
            "cross_function": True,
            "path": [OWNER, SINK],
            "role_owner": OWNER,
            "loop_magnitude": "grows_with_data",
        },
        health_impact=0.0,
        dimension="performance",
    )


def test_a_count_on_the_call_path_reaches_the_cause() -> None:
    queries = {SINK: QueryReading("grows", 101, 100), OWNER: QueryReading("ran", 3, None)}

    (opportunity,) = build_performance_opportunities([_finding()], queries=queries)

    assert opportunity.facets["queries"] == "grows"
    assert (opportunity.facets["query_statements"], opportunity.facets["query_items"]) == (
        "101",
        "100",
    )


def test_a_count_with_no_size_names_none() -> None:
    (opportunity,) = build_performance_opportunities(
        [_finding()], queries={OWNER: QueryReading("ran", 3, None)}
    )

    assert opportunity.facets["queries"] == "ran"
    assert "query_items" not in opportunity.facets


def test_counts_say_nothing_about_a_cause_off_the_database() -> None:
    (opportunity,) = build_performance_opportunities(
        [_finding("network")], queries={SINK: QueryReading("grows", 101, 100)}
    )

    assert "queries" not in opportunity.facets


def test_a_cause_inside_a_function_measured_hot_is_confirmed() -> None:
    (opportunity,) = build_performance_opportunities(
        [_finding("network")], measured={OWNER: "api-load"}, hot=frozenset({OWNER})
    )

    assert opportunity.facets["confirmed"] == "measured"


def test_growing_counts_confirm_first_and_a_ranked_function_alone_does_not() -> None:
    grows = {SINK: QueryReading("grows", 101, 100)}
    (exact,) = build_performance_opportunities(
        [_finding()], queries=grows, hot=frozenset({OWNER})
    )
    (ranked,) = build_performance_opportunities([_finding()], measured={OWNER: "api-load"})

    assert exact.facets["confirmed"] == "query_count"
    assert "confirmed" not in ranked.facets


def test_a_unit_test_run_marks_and_confirms_apart_and_a_leading_run_wins() -> None:
    (tests,) = build_performance_opportunities(
        [_finding("network")], in_tests={OWNER: "pytest"}, hot_in_tests=frozenset({OWNER})
    )
    assert (tests.facets["measured_in_tests"], tests.facets["confirmed"]) == (
        "pytest",
        "measured_in_tests",
    )
    assert "measured" not in tests.facets

    (both,) = build_performance_opportunities(
        [_finding("network")],
        measured={OWNER: "api-load"},
        hot=frozenset({OWNER}),
        in_tests={OWNER: "pytest"},
        hot_in_tests=frozenset({OWNER}),
    )
    assert (both.facets["measured"], both.facets["confirmed"]) == ("api-load", "measured")
    assert "measured_in_tests" not in both.facets


def test_a_cold_cause_a_leading_run_measured_is_confirmed_by_the_run() -> None:
    # The run answers the guess that a cold role runs once; counts do not.
    cold = _finding()
    cold.details["execution_role"] = "cli"
    grows = {SINK: QueryReading("grows", 101, 100)}
    (measured,) = build_performance_opportunities(
        [cold], queries=grows, measured={OWNER: "update"}, hot=frozenset({OWNER})
    )
    (counts,) = build_performance_opportunities([cold], queries=grows)

    assert measured.facets["execution_role"] == "cli"
    assert measured.facets["confirmed"] == "measured"
    assert counts.facets["confirmed"] == "query_count"
