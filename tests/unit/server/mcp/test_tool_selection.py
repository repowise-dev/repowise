"""Tests for the configurable MCP tool surface (_tool_selection)."""

from __future__ import annotations

import pytest

from repowise.core.registry import ToolEntry
from repowise.server.mcp_server._tool_selection import resolve_enabled_tools


def _fn(name):
    def f():  # pragma: no cover - never called
        return None

    f.__name__ = name
    return f


# A representative catalog: 3 plain defaults, 1 workspace-only, 1 opt-in.
CATALOG = [
    ToolEntry(_fn("get_answer"), "get_answer"),
    ToolEntry(_fn("get_context"), "get_context"),
    ToolEntry(_fn("list_repos"), "list_repos"),
    ToolEntry(_fn("get_blast_radius"), "get_blast_radius", requires_workspace=True),
    ToolEntry(_fn("get_dependency_path"), "get_dependency_path", default=False),
]


def test_default_single_repo_surface():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False)
    assert enabled == {"get_answer", "get_context", "list_repos"}


def test_default_workspace_adds_workspace_only():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=True)
    assert enabled == {"get_answer", "get_context", "list_repos", "get_blast_radius"}


def test_opt_in_tool_off_by_default():
    assert "get_dependency_path" not in resolve_enabled_tools(CATALOG, is_workspace=True)


def test_delta_add_and_remove():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="+get_dependency_path,-get_context"
    )
    assert enabled == {"get_answer", "list_repos", "get_dependency_path"}


def test_delta_string_or_list_equivalent():
    a = resolve_enabled_tools(CATALOG, is_workspace=False, override="+get_dependency_path")
    b = resolve_enabled_tools(CATALOG, is_workspace=False, override=["+get_dependency_path"])
    assert a == b == {"get_answer", "get_context", "list_repos", "get_dependency_path"}


def test_explicit_allowlist_replaces_default():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="get_answer,get_dependency_path"
    )
    assert enabled == {"get_answer", "get_dependency_path"}


def test_all_enables_everything_usable():
    assert resolve_enabled_tools(CATALOG, is_workspace=True, override="all") == {
        e.name for e in CATALOG
    }
    # In single-repo, "all" still excludes workspace-only tools.
    assert resolve_enabled_tools(CATALOG, is_workspace=False, override="all") == {
        "get_answer",
        "get_context",
        "list_repos",
        "get_dependency_path",
    }


def test_lean_profile_single_repo():
    # Intersected with the catalog: only the lean tools this registry has.
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False, override="lean")
    assert enabled == {"get_answer", "get_context"}


def test_lean_profile_workspace_adds_list_repos():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=True, override="LEAN")
    assert enabled == {"get_answer", "get_context", "list_repos"}


def test_lean_profile_full_registry():
    """Against the real registry, lean is exactly the agent-lean tools."""
    import repowise.server.mcp_server  # noqa: F401  (registers the tools)
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server._tool_selection import LEAN_TOOLS

    enabled = resolve_enabled_tools(
        mcp_tool_registry.entries(), is_workspace=False, override="lean"
    )
    assert enabled == LEAN_TOOLS
    assert "get_change_risk" not in enabled

    workspace = resolve_enabled_tools(
        mcp_tool_registry.entries(), is_workspace=True, override="lean"
    )
    assert workspace == LEAN_TOOLS | {"list_repos"}


def test_conformance_and_refactoring_are_opt_in():
    """generate_refactoring_code and get_conformance are off the default surface.

    Both must be named explicitly to appear; get_conformance stays workspace-gated
    even when opted in.
    """
    import repowise.server.mcp_server  # noqa: F401  (registers the tools)
    from repowise.core.registry import mcp_tool_registry

    entries = mcp_tool_registry.entries()

    single = resolve_enabled_tools(entries, is_workspace=False)
    workspace = resolve_enabled_tools(entries, is_workspace=True)
    for surface in (single, workspace):
        assert "generate_refactoring_code" not in surface
        assert "get_conformance" not in surface

    opted_ws = resolve_enabled_tools(
        entries, is_workspace=True, override="+generate_refactoring_code,+get_conformance"
    )
    assert {"generate_refactoring_code", "get_conformance"} <= opted_ws

    # In single-repo mode refactoring can be opted in, but conformance can't:
    # it needs the workspace graph, so an explicit mention is ignored there.
    opted_single = resolve_enabled_tools(
        entries, is_workspace=False, override="+generate_refactoring_code,+get_conformance"
    )
    assert "generate_refactoring_code" in opted_single
    assert "get_conformance" not in opted_single


def test_workspace_only_named_explicitly_is_dropped_single_repo():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=False, override="get_answer,get_blast_radius"
    )
    assert enabled == {"get_answer"}


def test_workspace_only_named_explicitly_kept_in_workspace():
    enabled = resolve_enabled_tools(
        CATALOG, is_workspace=True, override="get_answer,get_blast_radius"
    )
    assert enabled == {"get_answer", "get_blast_radius"}


def test_unknown_tool_ignored():
    enabled = resolve_enabled_tools(CATALOG, is_workspace=False, override="+does_not_exist")
    assert enabled == {"get_answer", "get_context", "list_repos"}


def test_empty_override_falls_back_to_default():
    assert resolve_enabled_tools(CATALOG, is_workspace=False, override="") == (
        resolve_enabled_tools(CATALOG, is_workspace=False)
    )


# --- live FastMCP trimming -------------------------------------------------


@pytest.mark.asyncio
async def test_apply_trims_and_restores_live_server(tmp_path):
    """apply_tool_selection trims the real server and can rebuild the full set."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import _tool_selection
    from repowise.server.mcp_server._tool_selection import apply_tool_selection

    mcp = mcp_mod.mcp
    (tmp_path / ".repowise").mkdir()

    async def names() -> set[str]:
        return {t.name for t in await mcp.list_tools()}

    try:
        # Single-repo default: workspace-only and opt-in tools are hidden.
        apply_tool_selection(mcp, repo_path=str(tmp_path), override=None)
        single = await names()
        assert "get_health" in single
        assert "get_blast_radius" not in single
        assert "get_dependency_path" not in single

        # Opt in to one tool; it reappears.
        apply_tool_selection(mcp, repo_path=str(tmp_path), override="+get_dependency_path")
        assert "get_dependency_path" in await names()
    finally:
        # Restore the full surface so other tests see every tool, including the
        # workspace-only ones an "all" override on a non-workspace path omits.
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
