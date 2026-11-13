"""Every MCP tool a Codex slash prompt names is one Codex is listed, or the prompt says how to list it.

Codex loads every tool schema up front, so with nothing configured it is
listed the core tools only. A prompt that sends it to another tool by name
sends it to a tool it cannot call.
"""

from __future__ import annotations

import re

from repowise.cli.agent_targets.targets import codex
from repowise.core.registry import mcp_tool_registry
from repowise.core.registry.tool_selection import resolve_enabled_tools


def _registered() -> list:
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    return mcp_tool_registry.entries()


def test_codex_prompts_name_only_listed_tools_or_say_how_to_list_them() -> None:
    entries = _registered()
    names = {entry.name for entry in entries}
    listed = resolve_enabled_tools(entries, is_workspace=False, client=codex.IDENTITY.slug)

    unlisted: dict[str, set[str]] = {}
    for prompt, text in codex.bundled_prompts():
        named = set(re.findall(r"\b[a-z_]+\b", text)) & names
        for tool in named - listed:
            if f"`+{tool}` to `mcp.tools`" not in text:
                unlisted.setdefault(prompt, set()).add(tool)

    assert not unlisted, f"Codex prompts naming tools Codex is not listed: {unlisted}"


def test_the_opt_in_line_names_a_tool_the_override_can_list() -> None:
    entries = _registered()
    listed = resolve_enabled_tools(
        entries, is_workspace=False, client=codex.IDENTITY.slug, override=["+get_dead_code"]
    )
    assert "get_dead_code" in listed
