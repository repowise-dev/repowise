"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_get_overview_prefers_curated_entry_points(setup_mcp, session):
    from repowise.core.persistence.models import KnowledgeGraphProjectMeta
    from repowise.server.mcp_server import get_overview

    # The fixture flags src/auth/service.py via the raw is_entry_point flag (a
    # high-pagerank sink). With a curated orientation list present, get_overview
    # must serve that instead of the flagged sink.
    session.add(
        KnowledgeGraphProjectMeta(
            repository_id="repo1",
            entry_points_json=json.dumps(["src/cli/main.py"]),
            entry_candidates_json=json.dumps(["src/cli/main.py"]),
        )
    )
    await session.commit()

    result = await get_overview()
    assert result["entry_points"] == ["src/cli/main.py"]


@pytest.mark.asyncio
async def test_get_overview(setup_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert result["title"] == "Test Repo Overview"
    assert "comprehensive test" in result["content_md"]
    assert "architecture_diagram_mermaid" not in result  # removed: not useful for agents
    assert len(result["key_modules"]) == 2
    assert any(m["name"] == "Auth Module" for m in result["key_modules"])
    assert "src/auth/service.py" in result["entry_points"]


@pytest.mark.asyncio
async def test_get_overview_with_repo_path(setup_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview(repo="/tmp/test-repo")
    assert result["title"] == "Test Repo Overview"


@pytest.mark.asyncio
async def test_get_overview_repo_not_found(setup_mcp):
    from repowise.server.mcp_server import get_overview

    with pytest.raises(LookupError, match="not found"):
        await get_overview(repo="/nonexistent")
