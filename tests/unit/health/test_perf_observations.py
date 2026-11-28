"""A cause whose marker has no fix strategy is an observation: no queue counts it."""

from __future__ import annotations

import json

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.actionability import NO_STRATEGY_FOR_MARKER
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.queue.counts import (
    OBSERVATION,
    perf_judgement,
    queue_counts,
)
from repowise.core.analysis.health.queue.eligibility import observation, perf_queue_counts
from tests.unit.health.fix_first_rows import PERFORMANCE, _perf


def _finding(marker: str, line: int = 4) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.MEDIUM,
        file_path="src/feed.py",
        function_name="build",
        line_start=line,
        line_end=line,
        details={"boundary_kind": None, "cross_function": False, "path": []},
        health_impact=0.0,
        dimension="performance",
    )


def _observation_row(opp: str = "perf_obs", **over) -> dict:
    return _perf(
        opp,
        "list.insert",
        intervention_symbol="src/feed.py::build",
        file_path="src/feed.py",
        biomarker_type="list_insert_zero_in_loop",
        boundary_kind=None,
        actionability_state="investigate",
        plan_state="no_safe_plan",
        fix_strategy=None,
        fix_safety=None,
        prerequisites=(NO_STRATEGY_FOR_MARKER,),
        **over,
    )


def test_a_marker_with_no_strategy_builds_an_observation() -> None:
    (cause,) = build_performance_opportunities([_finding("list_insert_zero_in_loop")])
    assert (cause.actionability_state, cause.fix) == ("investigate", None)
    assert cause.prerequisites == (NO_STRATEGY_FOR_MARKER,)
    assert observation(cause)


def test_a_cause_waiting_on_a_proof_is_no_observation() -> None:
    # Hoisting a construction has a strategy once the construction is proven
    # loop-invariant: still a cause to investigate.
    (cause,) = build_performance_opportunities([_finding("resource_construction_in_loop")])
    assert cause.actionability_state == "investigate"
    assert not observation(cause)
    assert not observation({"actionability_state": "expected", "prerequisites": ()})


def test_every_row_shape_reads_the_same_answer() -> None:
    stored = {"actionability_state": "investigate", "details": {"prerequisites": [NO_STRATEGY_FOR_MARKER]}}
    as_json = {
        "actionability_state": "investigate",
        "details_json": json.dumps({"prerequisites": [NO_STRATEGY_FOR_MARKER]}),
    }
    # The Fix first loader reads the key out of ``details_json`` in SQL, as text.
    as_sql = {"actionability_state": "investigate", "prerequisites": json.dumps([NO_STRATEGY_FOR_MARKER])}
    assert observation(stored) and observation(as_json) and observation(as_sql)


def test_an_observation_is_stored_as_such_and_no_count_holds_it() -> None:
    row = _observation_row()
    assert perf_judgement(row, {}, {}).reason == OBSERVATION
    without = perf_queue_counts(PERFORMANCE)
    assert perf_queue_counts([*PERFORMANCE, row]) == without
    before = queue_counts([(None, "now", 2), ("no_strategy", None, 1)], shown=2)
    after = queue_counts([(None, "now", 2), ("no_strategy", None, 1), (OBSERVATION, None, 4)], shown=2)
    assert after == before


def test_fix_first_leaves_an_observation_out_uncounted() -> None:
    without = build_fix_first(performance=PERFORMANCE, limit=None)
    with_obs = build_fix_first(performance=[*PERFORMANCE, _observation_row()], limit=None)
    assert with_obs.totals == without.totals
    assert [i.id for i in with_obs.items] == [i.id for i in without.items]
