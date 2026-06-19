"""Tests for the MCP tool-surface REST endpoints."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._tool_selection import (
    describe_tool_surface,
    set_tool_override,
)


def test_describe_tool_surface_single_repo(tmp_path):
    (tmp_path / ".repowise").mkdir()
    surface = describe_tool_surface(str(tmp_path))

    assert surface["is_workspace"] is False
    assert surface["override"] is None
    names = {t["name"] for t in surface["tools"]}
    # Core tools present and enabled; workspace-only present but disabled.
    assert {"get_answer", "get_context", "get_blast_radius"} <= names
    by_name = {t["name"]: t for t in surface["tools"]}
    assert by_name["get_answer"]["enabled"] is True
    assert by_name["get_blast_radius"]["requires_workspace"] is True
    assert by_name["get_blast_radius"]["enabled"] is False
    # Opt-in tools registered but off by default.
    assert by_name["get_execution_flows"]["default"] is False
    assert by_name["get_execution_flows"]["enabled"] is False
    # Every tool carries a one-line description.
    assert all(t["description"] for t in surface["tools"])


def test_set_tool_override_opt_in_and_clear(tmp_path):
    (tmp_path / ".repowise").mkdir()

    set_tool_override(str(tmp_path), ["+get_execution_flows"])
    surface = describe_tool_surface(str(tmp_path))
    by_name = {t["name"]: t for t in surface["tools"]}
    assert by_name["get_execution_flows"]["enabled"] is True
    assert surface["override"] == ["+get_execution_flows"]

    # Clearing removes the override and the mcp block entirely.
    set_tool_override(str(tmp_path), None)
    from repowise.core.repo_config import load_repo_config

    assert "mcp" not in load_repo_config(str(tmp_path))
    assert describe_tool_surface(str(tmp_path))["override"] is None


def test_set_tool_override_preserves_other_config(tmp_path):
    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "config.yaml").write_text("provider: openai\nmodel: gpt-5\n", encoding="utf-8")

    set_tool_override(str(tmp_path), ["+get_dependency_path"])

    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(str(tmp_path))
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-5"
    assert cfg["mcp"]["tools"] == ["+get_dependency_path"]


@pytest.mark.asyncio
async def test_router_get_and_patch(tmp_path, monkeypatch):
    """The REST endpoints resolve a repo path, read, and persist the surface."""
    from repowise.server.routers import mcp as mcp_router

    (tmp_path / ".repowise").mkdir()

    async def fake_repo_path(_request, repo_id):
        return str(tmp_path) if repo_id == "r1" else None

    monkeypatch.setattr(mcp_router, "_repo_path_for", fake_repo_path)

    got = await mcp_router.get_tool_surface(request=None, repo_id="r1")
    assert got.repo_id == "r1"
    assert any(t.name == "get_answer" and t.enabled for t in got.tools)

    from repowise.server.schemas import UpdateMcpToolsRequest

    updated = await mcp_router.update_tool_surface(
        body=UpdateMcpToolsRequest(repo_id="r1", tools=["+get_dependency_path"]),
        request=None,
    )
    assert any(t.name == "get_dependency_path" and t.enabled for t in updated.tools)


@pytest.mark.asyncio
async def test_router_patch_unknown_repo_404(monkeypatch):
    from fastapi import HTTPException

    from repowise.server.routers import mcp as mcp_router
    from repowise.server.schemas import UpdateMcpToolsRequest

    async def fake_repo_path(_request, repo_id):
        return None

    monkeypatch.setattr(mcp_router, "_repo_path_for", fake_repo_path)

    with pytest.raises(HTTPException) as exc:
        await mcp_router.update_tool_surface(
            body=UpdateMcpToolsRequest(repo_id="missing", tools=None),
            request=None,
        )
    assert exc.value.status_code == 404
