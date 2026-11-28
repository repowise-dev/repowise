"""Runtime evidence orders the performance queue; without it the order is static."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.perf.opportunity_rank import evidence_tier
from repowise.core.analysis.health.perf.serving import evidence_mark


@pytest.mark.parametrize(
    ("facets", "tier"),
    [
        ({"confirmed": "query_count"}, 0),
        ({"confirmed": "measured", "measured": "api"}, 0),
        ({"measured": "api"}, 1),
        ({"confirmed": "measured_in_tests", "measured_in_tests": "pytest"}, 0),
        ({"measured_in_tests": "pytest"}, 1),
        # Counter-evidence demotes nothing: the cause keeps its static place,
        # even where a workload measured it.
        ({"queries": "flat"}, 2),
        ({"queries": "flat", "measured": "api"}, 2),
        # Ranked against its cold name: kept, never led. Measured above the
        # lead bar by a leading workload, it leads: the name was a guess.
        ({"measured": "api", "measured_overrides": "cold_path"}, 2),
        ({"confirmed": "measured", "measured": "api", "measured_overrides": "cold_path"}, 0),
        ({"queries": "ran"}, 2),
        ({}, 2),
    ],
)
def test_evidence_tier(facets, tier) -> None:
    assert evidence_tier(facets) == tier


@pytest.mark.parametrize(
    ("facets", "workloads", "mark"),
    [
        ({"confirmed": "query_count", "queries": "grows"}, ["api"],
         {"state": "confirmed", "basis": "query_count"}),
        ({"queries": "flat", "measured": "api"}, ["api"],
         {"state": "counter_evidence", "basis": "query_count"}),
        ({"measured": "api"}, ["api"], {"state": "measured", "workload": "api"}),
        # Measured in tests: its own basis, and a test run never marks the
        # rest not exercised.
        ({"measured_in_tests": "pytest"}, [],
         {"state": "measured", "basis": "measured_in_tests", "workload": "pytest"}),
        ({"confirmed": "measured_in_tests", "measured_in_tests": "pytest"}, ["api"],
         {"state": "confirmed", "basis": "measured_in_tests"}),
        ({"measured": "api", "measured_in_tests": "pytest"}, ["api"],
         {"state": "measured", "workload": "api"}),
        ({"queries": "ran"}, [], {"state": "ran", "basis": "query_count"}),
        ({}, ["api", "bench"], {"state": "not_exercised", "workloads": ["api", "bench"]}),
        # No leading workload ran (none, or unit tests only): nothing to claim.
        ({}, [], None),
    ],
)
def test_evidence_mark(facets, workloads, mark) -> None:
    assert evidence_mark(facets, workloads) == mark
