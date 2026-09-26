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

TOOL_TIERS = frozenset({"canonical", "utility", "specialist"})
TOOL_SAFETY_KINDS = frozenset({"read_only", "generative", "mutating"})


def _supports_structured_output_kwarg(mcp: Any) -> bool:
    """Whether *mcp*'s ``tool()`` accepts ``structured_output=``.

    Older FastMCP releases predate the keyword; passing it there raises
    ``TypeError``. Probe the callable's signature, and let :meth:`apply`
    retry without the keyword if the probe cannot see through a wrapper,
    so the registry degrades to the previous behaviour instead of
    breaking registration.
    """
    import inspect

    tool = getattr(mcp, "tool", None)
    if tool is None:
        return False
    try:
        signature = inspect.signature(tool)
    except (TypeError, ValueError):
        return False
    if "structured_output" in signature.parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


_UNEXPECTED_KEYWORD_MARKERS = (
    "unexpected keyword argument 'structured_output'",
    "unexpected keyword argument \"structured_output\"",
)


def _rejects_structured_output(exc: TypeError) -> bool:
    """Whether *exc* is ``tool()`` refusing the ``structured_output`` keyword.

    CPython reports an unexpected keyword argument as ``tool() got an unexpected
    keyword argument 'structured_output'``; matching that specific form, instead of
    the bare words, keeps a ``TypeError`` raised inside ``tool()`` for any other
    reason travelling up to the caller, so a misconfigured server cannot look like
    a successful registration.
    """
    message = str(exc)
    return any(marker in message for marker in _UNEXPECTED_KEYWORD_MARKERS)


@dataclass(frozen=True)
class ToolRecipe:
    """Compact agent workflow contributed by a tool to the live registry."""

    name: str
    call: str
    requires: tuple[str, ...]


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
    tier: str = "canonical"
    surface_order: int = 1000
    trust_kind: str | None = None
    recipes: tuple[ToolRecipe, ...] = ()
    artifact_type: str = "generic"
    presentation: str = "generic"
    safety: str = "read_only"
    evidence_basis: str = "unknown"


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
        tier: str | None = None,
        surface_order: int = 1000,
        trust_kind: str | None = None,
        recipes: tuple[ToolRecipe, ...] = (),
        artifact_type: str = "generic",
        presentation: str = "generic",
        safety: str = "read_only",
        evidence_basis: str = "unknown",
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
            # Preserve the pre-tier extension API: a default workspace-only
            # tool was valid before tiers existed and is a utility by nature.
            resolved_tier = tier or (
                "utility"
                if default and requires_workspace
                else "canonical"
                if default
                else "specialist"
            )
            if resolved_tier not in TOOL_TIERS:
                raise ValueError(
                    f"unknown MCP tool tier {resolved_tier!r}; expected one of {sorted(TOOL_TIERS)}"
                )
            if resolved_tier == "canonical" and (not default or requires_workspace):
                raise ValueError("canonical MCP tools must be default and single-repo eligible")
            if resolved_tier == "specialist" and default:
                raise ValueError("specialist MCP tools must be opt-in (default=False)")
            if safety not in TOOL_SAFETY_KINDS:
                raise ValueError(
                    f"unknown MCP tool safety {safety!r}; "
                    f"expected one of {sorted(TOOL_SAFETY_KINDS)}"
                )
            if evidence_basis not in {"measured", "inferred", "unknown"}:
                raise ValueError("artifact evidence_basis must be measured, inferred, or unknown")
            self._entries.append(
                ToolEntry(
                    fn=fn,
                    name=fn.__name__,
                    default=default,
                    requires_workspace=requires_workspace,
                    tier=resolved_tier,
                    surface_order=surface_order,
                    trust_kind=trust_kind,
                    recipes=recipes,
                    artifact_type=artifact_type,
                    presentation=presentation,
                    safety=safety,
                    evidence_basis=evidence_basis,
                )
            )
            fn.__dict__["__repowise_trust_kind__"] = trust_kind
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

        Tools are registered with ``structured_output=False`` so each
        result carries its payload once, as a text block, instead of also
        duplicating it under ``structuredContent``. The SDK builds the
        output schema from the callable's return annotation, and the
        middleware layers snapshot ``__signature__`` with unevaluated
        annotations, so the SDK falls through to its wrapping case and
        serves ``{"result": <payload>}`` alongside the text. Disabling
        structured output removes that duplicate representation for every
        client.

        The keyword is only passed when the server's ``tool()`` accepts
        it — probed by signature and, failing that, by retrying the call
        without it — so older FastMCP releases keep working unchanged.
        A ``TypeError`` that does not name the keyword is a genuine
        failure and propagates unchanged.
        """
        if mcp in self._applied_to:
            return
        supports_structured_output = _supports_structured_output_kwarg(mcp)
        for entry in self._entries:
            wrapped = middleware(entry.fn) if middleware is not None else entry.fn
            if supports_structured_output:
                try:
                    decorator = mcp.tool(structured_output=False)
                except TypeError as exc:
                    if not _rejects_structured_output(exc):
                        raise
                    # Signature probing cannot see through every shim: a
                    # ``tool(**kwargs)`` wrapper reaches an older release
                    # that still rejects the keyword, so only the decorator
                    # factory is retried and the rest of the entries are
                    # registered plainly.
                    supports_structured_output = False
                    decorator = mcp.tool()
                decorator(wrapped)
            else:
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


__all__ = [
    "TOOL_SAFETY_KINDS",
    "TOOL_TIERS",
    "MCPToolRegistry",
    "ToolEntry",
    "ToolRecipe",
    "mcp_tool_registry",
]
