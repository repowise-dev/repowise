"""Page context maps to at most one prefetch, and the prefetch is a real tool turn."""

from __future__ import annotations

import json

import pytest

from repowise.core.registry import ToolEntry
from repowise.server.chat_grounding import (
    Grounding,
    plan_grounding,
    run_grounding,
)
from repowise.server.chat_tools import get_tool_catalog
from repowise.server.schemas.chat import ChatPageContext


async def _noop(**_kwargs) -> dict:
    return {}


def _entry(name: str, **fields) -> ToolEntry:
    return ToolEntry(fn=_noop, name=name, **fields)


ALL_TOOLS = [
    _entry("get_context", artifact_type="context", presentation="context"),
    _entry("get_symbol", artifact_type="source", presentation="source"),
    _entry("get_risk", artifact_type="risk", presentation="risk", evidence_basis="measured"),
    _entry("get_health", artifact_type="health", presentation="health"),
    _entry("get_why", artifact_type="decisions", presentation="decision_evidence"),
    _entry("get_change_risk", artifact_type="change_risk", presentation="change_risk"),
]


def _context(kind: str, target: str | None = None, target_kind: str | None = None):
    return ChatPageContext(kind=kind, label=kind.title(), target=target, target_kind=target_kind)


@pytest.mark.parametrize(
    ("kind", "target", "tool", "arguments"),
    [
        ("file", "src/a.py", "get_context", {"targets": ["src/a.py"]}),
        ("module", "src/pkg", "get_context", {"targets": ["src/pkg"]}),
        ("symbol", "src/a.py::run", "get_symbol", {"symbol_id": "src/a.py::run"}),
        ("risk", "src/a.py", "get_risk", {"targets": ["src/a.py"]}),
        ("health", "src/a.py", "get_health", {"targets": ["src/a.py"]}),
        ("decision", "d13e820c", "get_why", {"id": "d13e820c"}),
        ("commit", "abc1234", "get_change_risk", {"revspec": "abc1234"}),
    ],
)
def test_each_page_kind_maps_to_one_tool(kind, target, tool, arguments):
    plan = plan_grounding(_context(kind, target), ALL_TOOLS)
    assert plan is not None
    assert plan.tool_name == tool
    assert plan.arguments == arguments
    assert plan.target == target


def test_no_target_means_no_prefetch():
    assert plan_grounding(_context("file"), ALL_TOOLS) is None
    assert plan_grounding(_context("risk"), ALL_TOOLS) is None
    assert plan_grounding(_context("health", "   "), ALL_TOOLS) is None
    assert plan_grounding(None, ALL_TOOLS) is None


def test_unmapped_page_kinds_never_prefetch():
    for kind in ("repository", "overview", "documentation", "contributor", "settings", "chat"):
        assert plan_grounding(_context(kind, "anything"), ALL_TOOLS) is None


def test_tool_outside_the_enabled_surface_is_not_planned():
    assert plan_grounding(_context("symbol", "a.py::f"), [_entry("get_context")]) is None


def test_a_mutating_entry_is_never_prefetched():
    mutating = [_entry("get_context", safety="mutating")]
    assert plan_grounding(_context("file", "src/a.py"), mutating) is None


def test_multi_target_pages_split_into_a_target_list():
    plan = plan_grounding(_context("health", "src/a.py, src/b.py"), ALL_TOOLS)
    assert plan is not None
    assert plan.arguments == {"targets": ["src/a.py", "src/b.py"]}


def test_single_value_tools_take_the_first_target_only():
    plan = plan_grounding(_context("commit", "abc1234, def5678"), ALL_TOOLS)
    assert plan is not None
    assert plan.arguments == {"revspec": "abc1234"}


@pytest.mark.asyncio
async def test_run_grounding_returns_a_paired_tool_turn_and_envelope():
    calls: list[tuple[str, dict]] = []

    async def execute(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"targets": {"src/a.py": {"docs": {"title": "A"}}}}

    plan = plan_grounding(_context("file", "src/a.py"), ALL_TOOLS)
    grounding = await run_grounding(plan, execute)

    assert isinstance(grounding, Grounding)
    assert calls == [("get_context", {"targets": ["src/a.py"]})]

    assistant, tool = grounding.llm_messages()
    assert assistant["role"] == "assistant"
    call = assistant["tool_calls"][0]
    assert call["id"] == grounding.tool_id
    assert call["function"]["name"] == "get_context"
    assert json.loads(call["function"]["arguments"]) == {"targets": ["src/a.py"]}
    assert tool["role"] == "tool"
    assert tool["tool_call_id"] == grounding.tool_id
    assert json.loads(tool["content"])["targets"]["src/a.py"]["docs"]["title"] == "A"

    assert grounding.artifact["type"] == "context"
    assert grounding.artifact["tool_name"] == "get_context"
    assert grounding.artifact["data"]["targets"]["src/a.py"]["docs"]["title"] == "A"
    assert grounding.summary == "src/a.py"

    payload = grounding.sse_payload()
    assert payload["type"] == "grounding"
    assert payload["tool_id"] == grounding.tool_id
    assert payload["tool_name"] == "get_context"
    assert payload["input"] == {"targets": ["src/a.py"]}
    assert payload["artifact"] is grounding.artifact


@pytest.mark.asyncio
async def test_run_grounding_drops_a_failed_prefetch():
    async def execute(name: str, arguments: dict) -> dict:
        return {"error": "not indexed", "error_code": "tool_failed"}

    plan = plan_grounding(_context("symbol", "a.py::f"), ALL_TOOLS)
    assert await run_grounding(plan, execute) is None


@pytest.mark.asyncio
async def test_run_grounding_drops_a_raising_prefetch():
    async def execute(name: str, arguments: dict) -> dict:
        raise RuntimeError("boom")

    plan = plan_grounding(_context("decision", "abc"), ALL_TOOLS)
    assert await run_grounding(plan, execute) is None


@pytest.mark.asyncio
async def test_run_grounding_reads_artifact_metadata_from_the_live_registry():
    async def execute(name: str, arguments: dict) -> dict:
        return {"targets": {"src/a.py": {"trend": "increasing"}}}

    live = [tool.entry for tool in get_tool_catalog(None)]
    plan = plan_grounding(_context("risk", "src/a.py"), live)
    grounding = await run_grounding(plan, execute)
    assert grounding is not None
    assert grounding.artifact["type"] == "risk"
    assert grounding.artifact["presentation"] == "risk"
    assert grounding.artifact["evidence"]["basis"] == "measured"
