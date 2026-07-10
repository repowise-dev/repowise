"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_health_dashboard(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert result["mode"] == "dashboard"
    assert result["kpis"]["file_count"] == 2
    assert result["kpis"]["worst_performer_path"] == "src/auth/service.py"
    assert len(result["worst_files"]) == 2
    assert result["worst_files"][0]["file_path"] == "src/auth/service.py"
    assert len(result["top_findings"]) == 4


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_maintainability(setup_mcp, health_data):
    """The maintainability pillar is surfaced as a co-equal second signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    # Repo-level KPI headline for the maintainability pillar.
    # NLOC-weighted: (6.0*200 + 9.0*50) / 250 = 6.6.
    assert result["kpis"]["maintainability_average"] == 6.6
    # Per-file metrics carry all three dimension scores.
    worst = result["worst_files"][0]
    assert worst["defect_score"] == 4.5
    assert worst["maintainability_score"] == 6.0
    assert worst["performance_score"] == 9.0
    # Findings are tagged with their home pillar so they can be filtered.
    dims = {f["dimension"] for f in result["top_findings"]}
    assert dims == {"defect", "maintainability", "performance"}


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_performance(setup_mcp, health_data):
    """The performance pillar is surfaced as a co-equal third signal."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    # Repo-level KPI headline for the performance pillar.
    # NLOC-weighted: (9.0*200 + 10.0*50) / 250 = 9.2.
    assert result["kpis"]["performance_average"] == 9.2
    # The perf finding carries its boundary kind + cross-function reachability path.
    perf = [f for f in result["top_findings"] if f["dimension"] == "performance"]
    assert len(perf) == 1
    details = perf[0]["details"]
    assert details["boundary_kind"] == "db"
    assert details["cross_function"] is True
    assert details["path"] == [
        "src/auth/service.py::load_users",
        "src/db/models.py::fetch_one",
    ]


@pytest.mark.asyncio
async def test_get_health_dashboard_surfaces_leverage(setup_mcp, health_data):
    """Leverage view: which files move the NLOC-weighted headline, not just which score low."""
    from repowise.server.mcp_server import get_health

    result = await get_health()
    kpis = result["kpis"]
    # Weighted (4.5*200 + 8.5*50)/250 = 5.3 vs plain mean (4.5 + 8.5)/2 = 6.5:
    # the divergence is the "a big low file holds the headline down" signal.
    assert kpis["average_health"] == 5.3
    assert kpis["average_health_unweighted"] == 6.5
    assert kpis["average_health_weighting"] == "nloc"

    # Per-file leverage: (8 - score) * nloc, 0 once healthy.
    worst = next(m for m in result["worst_files"] if m["file_path"] == "src/auth/service.py")
    assert worst["weighted_deficit"] == 700  # (8.0 - 4.5) * 200
    healthy = next(m for m in result["worst_files"] if m["file_path"] == "src/db/models.py")
    assert healthy["weighted_deficit"] == 0  # score 8.5 >= Healthy floor

    # high_leverage_files excludes healthy files and leads with the biggest drag.
    hi = result["high_leverage_files"]
    assert [m["file_path"] for m in hi] == ["src/auth/service.py"]

    # gap_analysis: net points to move the *average* to 8.0 credits the healthy
    # file's surplus, so it's 8*250 - (4.5*200 + 8.5*50) = 2000 - 1325 = 675,
    # all sitting in one below-target file.
    gap = result["gap_analysis"]
    assert gap["target_score"] == 8.0
    assert gap["weighted_gap_points"] == 675
    assert gap["files_below_target"] == 1
    assert gap["files_to_reach_target"] == 1
    assert gap["files_for_half_gap"] == 1


@pytest.mark.asyncio
async def test_get_health_refactoring_capped_and_leverage_ranked(setup_mcp, health_data):
    """refactoring_plans is bounded by limit and reports the honest total."""
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["refactoring"], limit=5)
    assert "refactoring_plans" in result
    assert len(result["refactoring_plans"]) <= 5
    # Honest truncation signal is always present when refactoring is requested.
    assert "refactoring_plans_total" in result


@pytest.mark.asyncio
async def test_get_health_targeted(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    assert result["mode"] == "targets"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["max_ccn"] == 15
    assert result["metrics"][0]["maintainability_score"] == 6.0
    assert result["metrics"][0]["performance_score"] == 9.0
    assert all(f["file_path"] == "src/auth/service.py" for f in result["findings"])


@pytest.mark.asyncio
async def test_get_health_metric_carries_dominant_cause_and_magnitude(setup_mcp, health_data):
    """Metric rows lead with the worst finding + the pre-floor deduction sum."""
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    metric = result["metrics"][0]
    # Worst of {complex_method 1.2, nested 0.7, low_cohesion 1.0, io_in_loop 1.0}.
    assert metric["primary_biomarker"] == "complex_method"
    assert metric["primary_reason"] == "authenticate has cyclomatic complexity 15"
    # Σ health_impact = 1.2 + 0.7 + 1.0 + 1.0 — the depth behind a floored score.
    assert metric["total_deduction"] == pytest.approx(3.9)
    # Same lead reaches dashboard worst_files.
    dash = await get_health()
    worst = next(m for m in dash["worst_files"] if m["file_path"] == "src/auth/service.py")
    assert worst["primary_biomarker"] == "complex_method"
    assert worst["total_deduction"] == pytest.approx(3.9)
