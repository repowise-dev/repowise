"""What MCP tools each wired agent lists, as agents add and init's JSON report it."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.agent_targets.tool_surface import (
    agent_tool_surface,
    always_load_text,
    surface_text,
)
from repowise.core.registry.tool_selection import ALWAYS_LOAD_TOOLS
from repowise.core.settings import registry


def _config(repo: Path, text: str) -> None:
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


def test_defaults_per_client(tmp_path: Path) -> None:
    claude = agent_tool_surface(tmp_path, "claude-code")
    codex = agent_tool_surface(tmp_path, "codex")
    assert (claude["profile"], claude["tool_count"]) == ("standard", 10)
    assert (codex["profile"], codex["tool_count"]) == ("core", 5)
    assert codex["always_load"] is None
    assert claude["always_load"]["enabled"] is False
    assert claude["always_load"]["tools"] == sorted(ALWAYS_LOAD_TOOLS)
    assert claude["change_command"] == "repowise config set mcp.tools <core|lean|standard|all>"


def test_configured_profile_deltas_and_always_load(tmp_path: Path) -> None:
    _config(tmp_path, "mcp:\n  tools: [core, +get_health]\n  always_load: true\n")
    claude = agent_tool_surface(tmp_path, "claude-code")
    assert claude["profile"] == "core"
    assert claude["adjustments"] == ["+get_health"]
    assert claude["tool_count"] == 6
    assert surface_text(claude) == "lists 6 MCP tools (core profile +get_health)"
    assert claude["always_load"]["enabled"] is True
    assert always_load_text(claude).startswith("always-load is on for 4 read tools")


def test_an_exact_list_has_no_profile(tmp_path: Path) -> None:
    _config(tmp_path, "mcp:\n  tools: [get_answer, get_context]\n")
    surface = agent_tool_surface(tmp_path, "vscode")
    assert surface["profile"] is None
    assert surface["tool_count"] == 2
    assert surface_text(surface) == "lists 2 MCP tools (the exact mcp.tools list)"


def test_the_all_profile_counts_the_opt_in_tools(tmp_path: Path) -> None:
    _config(tmp_path, "mcp:\n  tools: all\n")
    assert agent_tool_surface(tmp_path, "codex")["tool_count"] > 10


def test_the_always_load_line_quotes_the_setting_cost(tmp_path: Path) -> None:
    cost = registry.get("mcp.always_load").cost_note
    assert cost
    assert cost in always_load_text(agent_tool_surface(tmp_path, "claude-code"))


def test_the_setting_names_every_read_tool() -> None:
    setting = registry.get("mcp.always_load")
    for name in ALWAYS_LOAD_TOOLS:
        assert name in setting.description
    assert setting.cost_note in setting.description


def test_deltas_alone_adjust_each_client_default(tmp_path: Path) -> None:
    _config(tmp_path, "mcp:\n  tools: [+get_health, +nope]\n")
    claude = agent_tool_surface(tmp_path, "claude-code")
    codex = agent_tool_surface(tmp_path, "codex")
    assert (claude["profile"], claude["tool_count"]) == ("standard", 10)
    assert (codex["profile"], codex["tool_count"]) == ("core", 6)
    # An unknown name is dropped by the server, so the line does not name it.
    assert codex["adjustments"] == ["+get_health"]
    # Standard already lists get_health: the addition changes nothing.
    assert claude["adjustments"] == []


def test_a_removal_that_changes_nothing_is_not_named(tmp_path: Path) -> None:
    _config(tmp_path, "mcp:\n  tools: [core, -bogus, -get_risk, -get_why, -get_why]\n")
    codex = agent_tool_surface(tmp_path, "codex")
    assert codex["adjustments"] == ["-get_why"]
    assert codex["tool_count"] == 4


def test_an_unreadable_config_reports_nothing(tmp_path: Path) -> None:
    _config(tmp_path, "mcp: [unclosed\n")
    assert agent_tool_surface(tmp_path, "codex") is None
