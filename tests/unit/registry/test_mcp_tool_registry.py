"""MCPToolRegistry behavior."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.registry import MCPToolRegistry


class _FakeMCP:
    """Minimal stand-in for FastMCP — captures ``mcp.tool()(fn)`` calls."""

    def __init__(self) -> None:
        self.registered: list[Any] = []

    def tool(self) -> Any:
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
