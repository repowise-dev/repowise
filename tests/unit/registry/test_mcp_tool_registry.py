"""MCPToolRegistry behavior."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.registry import MCPToolRegistry


class _FakeMCP:
    """Minimal stand-in for FastMCP — captures ``mcp.tool(**kw)(fn)`` calls."""

    def __init__(self) -> None:
        self.registered: list[Any] = []
        self.registration_kwargs: list[dict[str, Any]] = []

    def tool(self, **kwargs: Any) -> Any:
        self.registration_kwargs.append(kwargs)

        def _decorator(fn: Any) -> Any:
            self.registered.append(fn)
            return fn

        return _decorator


@pytest.fixture
def registry() -> MCPToolRegistry:
    return MCPToolRegistry()


def test_register_decorator_paren_form(registry):
    @registry.register()
    async def my_tool() -> dict:
        return {}

    assert my_tool in registry.tools()


def test_register_decorator_bare_form(registry):
    @registry.register
    async def my_tool() -> dict:
        return {}

    assert my_tool in registry.tools()


def test_tool_alias_matches_register(registry):
    @registry.tool()
    async def my_tool() -> dict:
        return {}

    assert my_tool in registry.tools()


def test_legacy_default_workspace_tool_infers_utility_tier(registry):
    @registry.tool(requires_workspace=True)
    async def workspace_utility() -> dict:
        return {}

    entry = registry.entries()[0]
    assert entry.default is True
    assert entry.requires_workspace is True
    assert entry.tier == "utility"


def test_apply_registers_with_server(registry):
    @registry.register
    async def t1() -> dict:
        return {}

    @registry.register
    async def t2() -> dict:
        return {}

    mcp = _FakeMCP()
    registry.apply(mcp)
    assert t1 in mcp.registered
    assert t2 in mcp.registered


def test_apply_is_idempotent_per_server(registry):
    @registry.register
    async def t1() -> dict:
        return {}

    mcp = _FakeMCP()
    registry.apply(mcp)
    registry.apply(mcp)
    assert mcp.registered.count(t1) == 1


def test_apply_supports_multiple_servers(registry):
    @registry.register
    async def t1() -> dict:
        return {}

    a = _FakeMCP()
    b = _FakeMCP()
    registry.apply(a)
    registry.apply(b)
    assert t1 in a.registered
    assert t1 in b.registered


def test_reset_clears_tools(registry):
    @registry.register
    async def t1() -> dict:
        return {}

    registry.reset()
    assert registry.tools() == []


def test_apply_passes_annotations_per_safety_kind(registry):
    from mcp.types import ToolAnnotations

    from repowise.core.registry.mcp_tool_registry import SAFETY_TO_ANNOTATION_HINTS

    @registry.register(safety="read_only")
    async def reader() -> dict:
        return {}

    @registry.register(safety="generative")
    async def generator() -> dict:
        return {}

    @registry.register(safety="mutating")
    async def mutator() -> dict:
        return {}

    mcp = _FakeMCP()
    registry.apply(
        mcp,
        annotations_for=lambda safety: ToolAnnotations(**SAFETY_TO_ANNOTATION_HINTS[safety]),
    )

    by_tool = dict(
        zip(
            [fn.__name__ for fn in mcp.registered],
            mcp.registration_kwargs,
            strict=True,
        )
    )
    assert by_tool["reader"]["annotations"] == ToolAnnotations(
        readOnlyHint=True, openWorldHint=False
    )
    assert by_tool["generator"]["annotations"] == ToolAnnotations(
        readOnlyHint=True, openWorldHint=True
    )
    assert by_tool["mutator"]["annotations"] == ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True
    )


def test_apply_annotations_reach_a_real_fastmcp_server(registry):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    @registry.register(safety="mutating")
    async def set_finding_status() -> dict:
        return {}

    server = FastMCP("test")
    from repowise.core.registry.mcp_tool_registry import SAFETY_TO_ANNOTATION_HINTS

    registry.apply(
        server,
        annotations_for=lambda safety: ToolAnnotations(**SAFETY_TO_ANNOTATION_HINTS[safety]),
    )

    import asyncio

    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == "set_finding_status")
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


def test_register_stores_available_when_as_is(registry):
    def needs_flows(facts) -> bool:
        return facts.counts.get("flows", 0) > 0

    @registry.register(available_when=needs_flows)
    async def gated() -> dict:
        return {}

    @registry.register
    async def plain() -> dict:
        return {}

    by_name = {entry.name: entry for entry in registry.entries()}
    assert by_name["gated"].available_when is needs_flows
    assert by_name["plain"].available_when is None
