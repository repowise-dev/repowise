"""The Codex session context names only what the tools Codex is listed are for."""

from __future__ import annotations

import re

import pytest

from repowise.cli.commands.augment_cmd import codex
from repowise.core.registry.tool_selection import listed_default_tools

_FULL = (
    "Use the repowise MCP tools for architecture overview, semantic search, "
    "implementation context, risk/hotspot checks, decision history, and dead-code analysis."
)


def _repo(tmp_path, tools: str | None):
    (tmp_path / ".repowise").mkdir(parents=True)
    if tools is not None:
        (tmp_path / ".repowise" / "config.yaml").write_text(f"mcp:\n  tools: {tools}\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("template", [codex._MCP_CONTEXT, codex._MCP_CONTEXT_WORKTREE])
@pytest.mark.parametrize("tools", [None, "core", "standard", "core +get_risk", "get_answer", "-get_why"])
def test_each_named_use_belongs_to_a_listed_tool(tmp_path, template, tools) -> None:
    text = codex._context(template, _repo(tmp_path, tools))
    listed = listed_default_tools(codex._TOOL_USES, client="codex", override=tools)
    for name, use in codex._TOOL_USES.items():
        assert (use in text) is (name in listed), text
    assert "{uses}" not in text and not re.search(r"tools for \.", text)


def test_the_full_surface_reads_as_before(tmp_path) -> None:
    assert _FULL in codex._context(codex._MCP_CONTEXT, _repo(tmp_path, "standard"))


def test_the_default_is_shorter_than_the_full_surface(tmp_path) -> None:
    default = codex._context(codex._MCP_CONTEXT, _repo(tmp_path / "a", None))
    full = codex._context(codex._MCP_CONTEXT, _repo(tmp_path / "b", "standard"))
    assert "semantic search, implementation context, and decision history" in default
    assert len(default) < len(full)
