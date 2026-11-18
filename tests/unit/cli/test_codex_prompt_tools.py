"""Every MCP tool a Codex prompt or skill names is one Codex is listed, or the text says how to list it.

Codex loads every tool schema up front, so with nothing configured it is
listed the core tools only. A prompt or skill that sends it to another tool by
name sends it to a tool it cannot call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.cli.agent_targets.targets import codex
from repowise.core.registry import mcp_tool_registry
from repowise.core.registry.tool_selection import resolve_enabled_tools

ROOT = Path(__file__).resolve().parents[3]


def _registered() -> list:
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    return mcp_tool_registry.entries()


def _codex_texts() -> list[tuple[str, str]]:
    skills = sorted((ROOT / "plugins" / "codex" / "skills").glob("*/SKILL.md"))
    return [
        *codex.bundled_prompts(),
        *((path.parent.name, path.read_text(encoding="utf-8")) for path in skills),
    ]


def test_codex_prompts_and_skills_name_only_listed_tools_or_say_how_to_list_them() -> None:
    entries = _registered()
    names = {entry.name for entry in entries}
    listed = resolve_enabled_tools(entries, is_workspace=False, client=codex.IDENTITY.slug)

    unlisted: dict[str, set[str]] = {}
    for item, text in _codex_texts():
        flat = " ".join(text.split())
        named = set(re.findall(r"\b[a-z_]+\b", text)) & names
        for tool in named - listed:
            if f"`+{tool}` to `mcp.tools`" not in flat:
                unlisted.setdefault(item, set()).add(tool)

    assert not unlisted, f"Codex prompts or skills naming tools Codex is not listed: {unlisted}"


def test_the_check_reads_the_codex_skills() -> None:
    assert {"change-review", "code-health", "pre-modification-check"} <= {
        item for item, _ in _codex_texts()
    }


@pytest.mark.parametrize("tool", ["get_dead_code", "get_overview"])
def test_the_opt_in_line_names_a_tool_the_override_can_list(tool: str) -> None:
    entries = _registered()
    listed = resolve_enabled_tools(
        entries, is_workspace=False, client=codex.IDENTITY.slug, override=[f"+{tool}"]
    )
    assert tool in listed
