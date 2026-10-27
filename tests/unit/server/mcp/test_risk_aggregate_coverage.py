"""get_risk PR mode with stored line coverage but no per-test map."""

from __future__ import annotations

from repowise.server.mcp_server.tool_risk.directives_pr import coverage_fields, missing_tests

_COVERAGE = {
    "status": "unavailable",
    "reason": "no_per_test_map_aggregate_available",
    "aggregate": {"file_count": 40, "ingested_commit": "old", "freshness": "current"},
}


def test_the_summary_names_the_aggregate_instead_of_unavailable_coverage():
    gaps = missing_tests({"test_gaps": ["src/a.py"]}, ["src/a.py"], _COVERAGE, None)

    assert gaps.usable is False
    assert gaps.population == []
    assert "aggregate line coverage is available" in gaps.summary
    assert "withheld" not in gaps.summary


def test_the_coverage_block_carries_the_aggregate():
    block = coverage_fields({}, _COVERAGE)["coverage"]

    assert block["reason"] == "no_per_test_map_aggregate_available"
    assert block["aggregate"]["file_count"] == 40


def test_without_stored_coverage_the_block_is_unchanged():
    block = coverage_fields({}, {"status": "unavailable"})["coverage"]

    assert block == {"status": "unavailable", "reason": "no_per_test_coverage_map"}
