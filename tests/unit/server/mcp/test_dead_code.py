"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_dead_code(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code()
    assert result["summary"]["total_findings"] == 3
    assert result["summary"]["safe_to_delete_count"] == 2

    # Tiered structure: dc1 (0.9) in high, dc2 (0.7) + dc3 (0.5) in medium
    tiers = result["tiers"]
    assert tiers["high"]["count"] == 1
    assert tiers["medium"]["count"] == 2
    assert tiers["low"]["count"] == 0

    # High tier findings sorted by confidence desc
    high_findings = tiers["high"]["findings"]
    assert high_findings[0]["confidence"] >= 0.8

    # Impact estimate present
    assert result["impact"]["total_lines_reclaimable"] > 0


@pytest.mark.asyncio
async def test_get_dead_code_safe_only(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(safe_only=True)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["safe_to_delete"] is True


@pytest.mark.asyncio
async def test_get_dead_code_by_kind(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(kind="unreachable_file", min_confidence=0.0)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["kind"] == "unreachable_file"


@pytest.mark.asyncio
async def test_get_dead_code_low_confidence(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(min_confidence=0.0)
    total = sum(t["count"] for t in result["tiers"].values())
    assert total == 3  # All 3 findings included


@pytest.mark.asyncio
async def test_get_dead_code_tier_filter(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(tier="high")
    assert "high" in result["tiers"]
    assert "medium" not in result["tiers"]
    assert "low" not in result["tiers"]
    assert result["tiers"]["high"]["count"] == 1


@pytest.mark.asyncio
async def test_get_dead_code_group_by_directory(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="directory", min_confidence=0.0)
    assert "by_directory" in result
    dirs = result["by_directory"]
    assert len(dirs) >= 1
    # Each dir entry has count, lines, safe_count
    for d in dirs:
        assert "directory" in d
        assert "count" in d
        assert "lines" in d


@pytest.mark.asyncio
async def test_get_dead_code_group_by_owner(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="owner", min_confidence=0.0)
    assert "by_owner" in result
    owners = result["by_owner"]
    assert len(owners) >= 1
    owner_names = [o["owner"] for o in owners]
    assert "Bob" in owner_names  # Bob owns dc2 + dc3
