"""Regression tests for ``MCPToolRegistry.apply`` server compatibility.

The registry registers tools with ``structured_output=False`` so payloads
cross the wire once. Older FastMCP releases do not accept that keyword, so
``apply`` must probe the server's ``tool()`` signature, retry without the
keyword when the probe cannot see through a wrapper, and degrade to a plain
``mcp.tool()`` call instead of raising ``TypeError``.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.registry.mcp_tool_registry import MCPToolRegistry


class _RecordingServer:
    """Minimal FastMCP stand-in that records how tools were registered."""

    def __init__(self, *, accept_structured_output: bool) -> None:
        self.accept_structured_output = accept_structured_output
        self.calls: list[dict[str, Any]] = []

    def tool(self, **kwargs: Any):
        if kwargs and not self.accept_structured_output:
            raise TypeError("tool() got an unexpected keyword argument 'structured_output'")
        self.calls.append(kwargs)

        def _decorator(fn):
            return fn

        return _decorator


def _make_registry() -> MCPToolRegistry:
    registry = MCPToolRegistry()

    @registry.register()
    async def sample_tool(arg: str) -> dict:
        return {"arg": arg}

    return registry


def test_apply_passes_structured_output_false_when_supported():
    registry = _make_registry()
    server = _RecordingServer(accept_structured_output=True)

    registry.apply(server)

    assert server.calls == [{"structured_output": False}]


def test_apply_falls_back_when_structured_output_unsupported():
    registry = _make_registry()
    server = _RecordingServer(accept_structured_output=False)

    registry.apply(server)

    assert server.calls == [{}]


def test_apply_is_idempotent_per_server():
    registry = _make_registry()
    server = _RecordingServer(accept_structured_output=True)

    registry.apply(server)
    registry.apply(server)

    assert len(server.calls) == 1


def test_apply_supports_multiple_servers():
    registry = _make_registry()
    first = _RecordingServer(accept_structured_output=True)
    second = _RecordingServer(accept_structured_output=False)

    registry.apply(first)
    registry.apply(second)

    assert first.calls == [{"structured_output": False}]
    assert second.calls == [{}]


def test_applied_tools_serve_no_structured_content():
    """Pin the wire effect of the keyword against a real FastMCP server.

    ``func_metadata`` builds a tool's output schema from the callable it is
    handed, so the registration keyword — not the tool function — is what
    drops the duplicate ``{"result": ...}`` ``structuredContent``. Without an
    output schema there is nothing to wrap, and a client receives the text
    block alone. A server that silently ignored the keyword would leave every
    payload crossing the wire twice.
    """
    import asyncio

    from mcp.server.fastmcp import FastMCP

    registry = _make_registry()
    server = FastMCP("compat")

    registry.apply(server)

    tools = asyncio.run(server.list_tools())
    assert [(tool.name, tool.outputSchema) for tool in tools] == [("sample_tool", None)]


class _BrokenServer:
    """Stand-in whose ``tool()`` fails for a reason unrelated to the keyword."""

    def tool(self, **kwargs: Any):
        raise TypeError("tool() missing 1 required keyword-only argument: 'fn'")


def test_apply_propagates_unrelated_typeerror():
    """A ``TypeError`` that does not name the keyword must not be swallowed."""
    registry = _make_registry()

    with pytest.raises(TypeError, match="required keyword-only argument"):
        registry.apply(_BrokenServer())


class _NoisyServer:
    """Stand-in whose ``tool()`` fails for a reason that names the keyword."""

    def tool(self, **kwargs: Any):
        raise TypeError("structured_output needs a keyword argument map")


def test_apply_propagates_typeerror_that_only_mentions_the_keyword():
    """Mentioning the keyword is not the same as rejecting it."""
    registry = _make_registry()

    with pytest.raises(TypeError, match="needs a keyword argument map"):
        registry.apply(_NoisyServer())
