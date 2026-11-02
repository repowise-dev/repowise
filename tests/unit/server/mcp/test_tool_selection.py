"""MCP tool surface against the real registry and the live FastMCP server.

The pure selection rules are tested in tests/unit/registry/test_mcp_tool_selection.py.
"""

from __future__ import annotations

import pytest

from repowise.core.registry.tool_selection import resolve_enabled_tools


def test_lean_profile_full_registry():
    """Against the real registry, lean is exactly the agent-lean tools."""
    from repowise.core.registry import mcp_tool_registry
    from repowise.core.registry.tool_selection import LEAN_TOOLS
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()  # tool modules import lazily
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
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()  # tool modules import lazily
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


def test_real_registry_phase_one_surfaces_are_exact():
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    canonical = {
        "get_answer",
        "get_context",
        "get_symbol",
        "search_codebase",
        "get_risk",
        "get_change_risk",
        "get_why",
        "get_overview",
        "get_health",
        "get_dead_code",
    }
    ensure_full_surface()
    entries = mcp_tool_registry.entries()
    assert {entry.name for entry in entries if entry.tier == "canonical"} == canonical
    assert resolve_enabled_tools(entries, is_workspace=False) == canonical
    assert resolve_enabled_tools(entries, is_workspace=True) == canonical | {"list_repos"}


def test_real_registry_specialist_eligibility_is_mode_specific():
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    entries = mcp_tool_registry.entries()
    single_all = resolve_enabled_tools(entries, is_workspace=False, override="all")
    workspace_all = resolve_enabled_tools(entries, is_workspace=True, override="all")
    assert {
        "get_dependency_path",
        "get_execution_flows",
        "generate_refactoring_code",
    } <= single_all
    assert {
        "get_architecture",
        "get_blast_radius",
        "get_conformance",
        "list_repos",
    }.isdisjoint(single_all)
    assert {entry.name for entry in entries} == workspace_all


# --- live FastMCP trimming -------------------------------------------------


@pytest.mark.asyncio
async def test_apply_trims_and_restores_live_server(tmp_path, monkeypatch):
    """apply_tool_selection trims the real server and can rebuild the full set."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import _tool_selection
    from repowise.server.mcp_server._tool_selection import apply_tool_selection

    mcp = mcp_mod.mcp
    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)

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
        from repowise.server.mcp_server.tool_overview import _tool_surface_guide

        guide = _tool_surface_guide(is_workspace=False)
        assert set(guide["enabled"]) == await names()
        assert "get_dependency_path" in guide["enabled"]
    finally:
        # Restore the full surface so other tests see every tool, including the
        # workspace-only ones an "all" override on a non-workspace path omits.
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


# --- available_when, boot and settings ------------------------------------


def _gate(monkeypatch, name: str) -> None:
    """Gate one live entry on a ``flows`` fact the default facts do not carry."""
    from dataclasses import replace

    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    gated = [
        replace(e, available_when=lambda f: f.counts.get("flows", 0) > 0) if e.name == name else e
        for e in mcp_tool_registry.entries()
    ]
    monkeypatch.setattr(mcp_tool_registry, "entries", lambda: list(gated))


def test_boot_selection_evaluates_predicates(tmp_path, monkeypatch):
    from repowise.server.mcp_server import _tool_selection

    _gate(monkeypatch, "get_health")
    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)
    monkeypatch.setattr(_tool_selection, "_selected_surface", None)

    enabled = _tool_selection.apply_tool_selection(object(), repo_path=str(tmp_path))

    assert "get_health" not in enabled
    assert "get_answer" in enabled


def test_settings_surface_marks_a_gated_tool_ineligible(tmp_path, monkeypatch):
    from repowise.server.mcp_server import _tool_selection

    _gate(monkeypatch, "get_health")
    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)

    rows = {
        row["name"]: row for row in _tool_selection.describe_tool_surface(str(tmp_path))["tools"]
    }

    assert rows["get_health"]["eligible"] is False
    assert rows["get_health"]["enabled"] is False
    assert rows["get_answer"]["eligible"] is True
