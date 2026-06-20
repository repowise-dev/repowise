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
async def test_get_health_targeted(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    assert result["mode"] == "targets"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["max_ccn"] == 15
    assert result["metrics"][0]["maintainability_score"] == 6.0
    assert result["metrics"][0]["performance_score"] == 9.0
    assert all(f["file_path"] == "src/auth/service.py" for f in result["findings"])
