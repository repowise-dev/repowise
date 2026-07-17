"""The CLAUDE.md tool table must track the live MCP tool registry.

The table used to be hand-edited prose inside claude_md.j2 and silently
drifted from the registered surface. It now lives in
``editor_files/tool_table.py``; this test pins it to the registry.
"""

from __future__ import annotations

from repowise.core.generation.editor_files.tool_table import (
    TOOL_TABLE_ROWS,
    render_tool_table,
)
from repowise.core.registry import mcp_tool_registry


def _registered_names() -> set[str]:
    import repowise.server.mcp_server  # noqa: F401  (registers the tools)

    return {fn.__name__ for fn in mcp_tool_registry.tools()}


def test_every_table_row_names_a_registered_tool():
    unknown = set(TOOL_TABLE_ROWS) - _registered_names()
    assert not unknown, f"tool_table.py rows for unregistered tools: {unknown}"


def test_core_default_surface_is_documented():
    # The single-repo default surface an agent actually sees (list_repos is
    # workspace plumbing and deliberately has no row).
    core = {
        "get_answer",
        "get_context",
        "get_symbol",
        "search_codebase",
        "get_overview",
        "get_risk",
        "get_change_risk",
        "get_why",
        "get_dead_code",
        "get_health",
    }
    missing = core - set(TOOL_TABLE_ROWS)
    assert not missing, f"default-surface tools missing a table row: {missing}"


def test_render_produces_one_row_per_tool():
    md = render_tool_table()
    assert md.startswith("| Tool | When and why |")
    # Header + separator + one line per row.
    assert len(md.splitlines()) == 2 + len(TOOL_TABLE_ROWS)
