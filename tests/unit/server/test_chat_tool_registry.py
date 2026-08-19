"""Drift guard: chat tool registry stays a documented 7-tool subset."""

from __future__ import annotations

from repowise.server.chat_tools import get_tool_registry


def test_chat_tool_registry_is_the_documented_seven() -> None:
    names = set(get_tool_registry())
    assert names == {
        "get_overview",
        "get_context",
        "get_risk",
        "get_change_risk",
        "get_why",
        "search_codebase",
        "get_dead_code",
    }


def test_chat_dead_code_schema_default_matches_risk_cap() -> None:
    from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
    from repowise.server.chat_tools import get_tool_registry

    params = get_tool_registry()["get_dead_code"].parameters["properties"]["min_confidence"]
    assert params["default"] == RISK_CAP_CONFIDENCE


def test_chat_get_context_include_matches_cli_blocks() -> None:
    """Chat schema must advertise the same include blocks MCP/CLI accept.

    A stale enum that listed docs/freshness/source and omitted skeleton/health
    made the chat model request a no-op ``source`` block and never ask for
    skeleton or health — both of which get_context actually serves.
    """
    from repowise.cli.commands.context_cmd import _INCLUDE_BLOCKS
    from repowise.server.chat_tools import get_tool_registry

    props = get_tool_registry()["get_context"].parameters["properties"]
    enum = props["include"]["items"]["enum"]
    assert set(enum) == set(_INCLUDE_BLOCKS)
    assert "compact" in props
    assert props["compact"]["default"] is True
    for bogus in ("docs", "freshness", "source"):
        assert bogus not in enum
