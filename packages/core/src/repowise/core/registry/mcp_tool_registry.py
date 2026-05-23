"""MCP tool registry — collect tool functions, register them with FastMCP.

The OSS MCP tools previously decorated themselves with ``@mcp.tool()``
directly, which means every tool module had a hard import dependency on
the module-level ``mcp`` singleton in
:mod:`repowise.server.mcp_server._server`. A third-party package adding
its own tools either had to monkey-patch that singleton or replicate the
import wiring.

This registry inverts the dependency. Each tool decorates itself with
:meth:`MCPToolRegistry.register`, which stores the function without
binding it to any server instance. When the server boots, it calls
:meth:`apply` once and the registry attaches every collected tool to the
:class:`FastMCP` instance — same effect as the old decorator, but the
binding now happens at the call site instead of at import time.

Usage::

    # Tool side
    from repowise.core.registry import mcp_tool_registry

    @mcp_tool_registry.register()
    async def get_thing(arg: str) -> dict:
        ...

    # Server side
    from repowise.core.registry import mcp_tool_registry
    mcp = FastMCP("repowise")
    mcp_tool_registry.apply(mcp)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MCPToolRegistry:
    """Holds tool callables until :meth:`apply` attaches them to a server."""

    def __init__(self) -> None:
        self._tools: list[Callable[..., Any]] = []
        self._applied_to: list[Any] = []

    def register(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]] | Callable[..., Any]:
        """Decorator that schedules a function for FastMCP registration.

        Supports both decorator forms — bare ``@register`` and
        ``@register()`` — so call sites read naturally. Keyword arguments
        are currently ignored; they exist as a forward-compat hook for
        future ``description=`` / ``name=`` overrides.
        """
        # Bare-decorator form: @mcp_tool_registry.register
        if len(args) == 1 and callable(args[0]) and not kwargs:
            fn = args[0]
            self._tools.append(fn)
            return fn

        # Called form: @mcp_tool_registry.register()
        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools.append(fn)
            return fn

        return _decorator

    # FastMCP-style alias so existing tool modules can swap
    # ``from ._server import mcp`` to
    # ``from repowise.core.registry import mcp_tool_registry as mcp``
    # with no other changes — the ``@mcp.tool()`` call still works.
    tool = register

    def apply(self, mcp: Any) -> None:
        """Attach every registered tool to *mcp* via ``mcp.tool()``.

        Calling :meth:`apply` multiple times with the same server is a
        no-op on subsequent calls; calling it with a different server
        registers everything against that server too (useful for tests
        that spin up isolated :class:`FastMCP` instances).
        """
        if mcp in self._applied_to:
            return
        for fn in self._tools:
            mcp.tool()(fn)
        self._applied_to.append(mcp)

    def reset(self) -> None:
        """Drop every registered tool. Used by tests."""
        self._tools.clear()
        self._applied_to.clear()

    def tools(self) -> list[Callable[..., Any]]:
        """Return every registered tool function. Used by tests."""
        return list(self._tools)


mcp_tool_registry = MCPToolRegistry()
"""Process-wide default registry used by the OSS MCP server."""


__all__ = ["MCPToolRegistry", "mcp_tool_registry"]
