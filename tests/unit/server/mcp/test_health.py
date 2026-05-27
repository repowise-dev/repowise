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
    assert len(result["top_findings"]) == 2


@pytest.mark.asyncio
async def test_get_health_targeted(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"])
    assert result["mode"] == "targets"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["max_ccn"] == 15
    assert all(f["file_path"] == "src/auth/service.py" for f in result["findings"])
