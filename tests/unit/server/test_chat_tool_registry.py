"""Chat consumes the configured MCP registry without maintaining a copy."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from repowise.core.registry import ToolEntry, mcp_tool_registry
from repowise.core.registry.tool_selection import AvailabilityFacts, resolve_enabled_tools
from repowise.server import chat_tools
from repowise.server.mcp_server import _tool_selection, ensure_full_surface


def test_chat_catalog_matches_the_selected_mcp_surface(tmp_path) -> None:
    ensure_full_surface()
    selected = resolve_enabled_tools(
        mcp_tool_registry.entries(),
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


def _gate_get_health_on(monkeypatch, fact: str) -> None:
    """Make the live get_health entry available only when *fact* is counted."""
    gated = [
        replace(entry, available_when=lambda facts: facts.counts.get(fact, 0) > 0)
        if entry.name == "get_health"
        else entry
        for entry in mcp_tool_registry.entries()
    ]
    monkeypatch.setattr(mcp_tool_registry, "entries", lambda: list(gated))


def test_chat_hides_a_tool_whose_predicate_fails(tmp_path, monkeypatch) -> None:
    ensure_full_surface()
    _gate_get_health_on(monkeypatch, "findings")
    monkeypatch.setattr(_tool_selection, "_availability_facts", lambda _p: AvailabilityFacts())

    names = {tool.entry.name for tool in chat_tools.get_tool_catalog(str(tmp_path))}

    assert "get_health" not in names
    assert "get_answer" in names


def test_chat_reevaluates_the_predicate_every_turn(tmp_path, monkeypatch) -> None:
    ensure_full_surface()
    _gate_get_health_on(monkeypatch, "findings")
    facts = {"current": AvailabilityFacts()}
    monkeypatch.setattr(_tool_selection, "_availability_facts", lambda _p: facts["current"])

    before = {tool.entry.name for tool in chat_tools.get_tool_catalog(str(tmp_path))}
    facts["current"] = AvailabilityFacts(counts={"findings": 3})
    after = {tool.entry.name for tool in chat_tools.get_tool_catalog(str(tmp_path))}

    assert "get_health" not in before
    assert "get_health" in after


@pytest.mark.asyncio
async def test_chat_refuses_to_execute_a_gated_tool(tmp_path, monkeypatch) -> None:
    ensure_full_surface()
    _gate_get_health_on(monkeypatch, "findings")
    monkeypatch.setattr(_tool_selection, "_availability_facts", lambda _p: AvailabilityFacts())

    result = await chat_tools.execute_tool("get_health", {}, repo_path=str(tmp_path))

    assert result["error_code"] == "tool_not_enabled"


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
