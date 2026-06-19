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
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolEntry:
    """A registered tool plus the metadata that drives surface selection.

    ``name`` is the tool's wire name (the function ``__name__``, which is
    what FastMCP registers and what the selection layer removes by). ``default``
    marks whether the tool is part of the curated default surface; opt-in tools
    set it ``False``. ``requires_workspace`` marks tools that only do useful
    work in workspace mode, so they are hidden from single-repo servers.
    """

    fn: Callable[..., Any]
    name: str
    default: bool = True
    requires_workspace: bool = False


class MCPToolRegistry:
    """Holds tool callables until :meth:`apply` attaches them to a server."""

    def __init__(self) -> None:
        self._entries: list[ToolEntry] = []
        self._applied_to: list[Any] = []

    def register(
        self,
        *args: Any,
        default: bool = True,
        requires_workspace: bool = False,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]] | Callable[..., Any]:
        """Decorator that schedules a function for FastMCP registration.

        Supports both decorator forms — bare ``@register`` and
        ``@register()`` — so call sites read naturally. ``default`` and
        ``requires_workspace`` annotate the tool for the selection layer
        (see :class:`ToolEntry`); any other keyword arguments are reserved
        for future ``description=`` / ``name=`` overrides and ignored.
        """

        def _add(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._entries.append(
                ToolEntry(
                    fn=fn,
                    name=fn.__name__,
                    default=default,
                    requires_workspace=requires_workspace,
                )
            )
            return fn

        # Bare-decorator form: @mcp_tool_registry.register
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return _add(args[0])

        # Called form: @mcp_tool_registry.register()
        return _add

    # FastMCP-style alias so existing tool modules can swap
    # ``from ._server import mcp`` to
    # ``from repowise.core.registry import mcp_tool_registry as mcp``
    # with no other changes — the ``@mcp.tool()`` call still works.
    tool = register

    def apply(
        self,
        mcp: Any,
        middleware: Callable[[Callable[..., Any]], Callable[..., Any]] | None = None,
    ) -> None:
        """Attach every registered tool to *mcp* via ``mcp.tool()``.

        Calling :meth:`apply` multiple times with the same server is a
        no-op on subsequent calls; calling it with a different server
        registers everything against that server too (useful for tests
        that spin up isolated :class:`FastMCP` instances).

        *middleware*, when given, wraps each tool function before
        registration — the server passes its savings instrumentation in
        this way so the registry stays decoupled from it. A signature-
        preserving wrapper is the caller's responsibility (FastMCP reads
        each tool's signature to build its schema). Defaults to identity.
        """
        if mcp in self._applied_to:
            return
        for entry in self._entries:
            wrapped = middleware(entry.fn) if middleware is not None else entry.fn
            mcp.tool()(wrapped)
        self._applied_to.append(mcp)

    def reset(self) -> None:
        """Drop every registered tool. Used by tests."""
        self._entries.clear()
        self._applied_to.clear()

    def tools(self) -> list[Callable[..., Any]]:
        """Return every registered tool function. Used by tests."""
        return [entry.fn for entry in self._entries]

    def entries(self) -> list[ToolEntry]:
        """Return every registered tool with its selection metadata."""
        return list(self._entries)


mcp_tool_registry = MCPToolRegistry()
"""Process-wide default registry used by the OSS MCP server."""


__all__ = ["MCPToolRegistry", "ToolEntry", "mcp_tool_registry"]
