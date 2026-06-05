"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_risk_single_target(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["hotspot_score"] == 0.92
    assert t["dependents_count"] >= 1  # middleware imports it
    assert len(t["co_change_partners"]) == 2
    assert t["primary_owner"] == "Alice"
    assert t["owner_pct"] == 0.65
    assert "risk_summary" in t
    assert "hotspot score" in t["risk_summary"]

    # Trend: 30d=3, 90d=8 → baseline_rate=0.083, recent=0.1 → stable
    assert t["trend"] in ("increasing", "stable", "decreasing")

    # Risk type: churn_percentile=0.92, no fix keywords → churn-heavy
    assert t["risk_type"] == "churn-heavy"

    # Impact surface: middleware.py depends on service.py
    assert len(t["impact_surface"]) >= 1
    impact_files = [s["file_path"] for s in t["impact_surface"]]
    assert "src/auth/middleware.py" in impact_files
    # Each entry has pagerank and is_entry_point
    for s in t["impact_surface"]:
        assert "pagerank" in s
        assert "is_entry_point" in s


@pytest.mark.asyncio
async def test_get_risk_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py", "src/db/models.py"])
    targets = result["targets"]
    assert len(targets) == 2
    assert "global_hotspots" in result
    # Both targets should have trend and risk_type
    for t in targets.values():
        assert "trend" in t
        assert "risk_type" in t


@pytest.mark.asyncio
async def test_get_risk_global_hotspots_exclude_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    # service.py is a hotspot but should NOT appear in global_hotspots
    for h in result["global_hotspots"]:
        assert h["file_path"] != "src/auth/service.py"


@pytest.mark.asyncio
async def test_get_risk_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/middleware.py"])
    t = result["targets"]["src/auth/middleware.py"]
    assert t["hotspot_score"] == 0.0  # No git metadata for this file
    assert t["trend"] == "unknown"
    assert "risk_summary" in t
    # Impact surface and risk_type still computed from graph data
    assert "risk_type" in t
    assert "impact_surface" in t


@pytest.mark.asyncio
async def test_get_risk_stable_file(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/db/models.py"])
    t = result["targets"]["src/db/models.py"]
    # 0 commits in 30d and 90d → stable
    assert t["trend"] == "stable"
    # churn_percentile=0.15, dep_count=1, no fix keywords → stable
    assert t["risk_type"] == "stable"


# ---- _classify_risk_type small-team calibration (issue #361) ---------------


def _bus_factor_meta():
    from types import SimpleNamespace

    return SimpleNamespace(
        significant_commits_json="[]",
        churn_percentile=0.3,
        bus_factor=1,
        commit_count_total=40,
        is_hotspot=False,
    )


def test_classify_bus_factor_risk_on_normal_team():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=8) == "bus-factor-risk"


def test_classify_bus_factor_suppressed_on_small_team():
    """A single-author file is the expected shape of a 1-3 person repo —
    not a bus-factor warning unless the file is hotspot-active."""
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=2) == "stable"


def test_classify_bus_factor_kept_on_small_team_hotspot():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    meta = _bus_factor_meta()
    meta.is_hotspot = True
    assert _classify_risk_type(meta, dep_count=1, team_size=2) == "bus-factor-risk"


def test_classify_bus_factor_unknown_team_size_keeps_behaviour():
    from repowise.server.mcp_server.tool_risk import _classify_risk_type

    assert _classify_risk_type(_bus_factor_meta(), dep_count=1, team_size=None) == "bus-factor-risk"
