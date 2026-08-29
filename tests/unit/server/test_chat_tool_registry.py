"""Chat consumes the configured MCP registry without maintaining a copy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repowise.core.registry import ToolEntry
from repowise.server import chat_tools
from repowise.server.mcp_server import _tool_selection, ensure_full_surface


def test_chat_catalog_matches_the_selected_mcp_surface(tmp_path) -> None:
    ensure_full_surface()
    selected = _tool_selection.resolve_enabled_tools(
        _tool_selection.mcp_tool_registry.entries(),
        is_workspace=_tool_selection._is_workspace(str(tmp_path)),
        override=None,
    )

    catalog = chat_tools.get_tool_catalog(str(tmp_path))

    assert {tool.entry.name for tool in catalog} == selected


def test_chat_honors_the_repository_mcp_allowlist(tmp_path) -> None:
    ensure_full_surface()
    with patch.object(
        _tool_selection,
        "_read_config_override",
        return_value=["get_answer", "get_health"],
    ):
        catalog = chat_tools.get_tool_catalog(str(tmp_path))

    assert [tool.entry.name for tool in catalog] == ["get_answer", "get_health"]


def test_chat_llm_schemas_are_the_fastmcp_generated_schemas(tmp_path) -> None:
    ensure_full_surface()
    schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in chat_tools.get_tool_schemas_for_llm(str(tmp_path))
    }

    for name, parameters in schemas.items():
        assert parameters == _tool_selection.get_registered_tool(name).parameters


def test_artifact_and_evidence_metadata_come_from_registry(tmp_path) -> None:
    catalog = {tool.entry.name: tool.entry for tool in chat_tools.get_tool_catalog(str(tmp_path))}

    assert catalog["get_risk"].artifact_type == "risk"
    assert catalog["get_risk"].presentation == "risk"
    assert catalog["get_risk"].evidence_basis == "measured"
    assert catalog["search_codebase"].evidence_basis == "inferred"


@pytest.mark.asyncio
async def test_mutating_tools_require_an_explicit_confirmation() -> None:
    called = False

    async def mutate() -> dict:
        nonlocal called
        called = True
        return {"changed": True}

    entry = ToolEntry(fn=mutate, name="mutate", safety="mutating")

    result = await chat_tools.execute_entry(entry, {}, confirmed=False)

    assert called is False
    assert result["error_code"] == "confirmation_required"
    assert result["requires_confirmation"] is True

    result = await chat_tools.execute_entry(entry, {}, confirmed=True)
    assert called is True
    assert result == {"changed": True}
