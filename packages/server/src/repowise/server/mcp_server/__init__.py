"""repowise MCP Server — a curated, configurable tool surface for AI agents.

By default a single-repo server exposes ten tools (get_answer, get_context,
get_symbol, search_codebase, get_overview, get_risk, get_change_risk, get_why,
get_dead_code, get_health). Workspace mode also exposes the ``list_repos``
discovery utility by default. Six specialist tools are registered but off by
default and can be opted in via the ``mcp.tools`` config block or the
``repowise mcp --tools`` flag; architecture, blast radius, and conformance are
workspace-only. The selection layer lives in :mod:`._tool_selection`.

Exposes the full repowise wiki as queryable tools via the MCP protocol.
Supports stdio transport (Claude Code, Cursor, Cline), streamable HTTP, and
legacy SSE transport.

Tool modules load lazily (see :func:`ensure_full_surface`), so importing this
package, or one tool out of it, does not drag in the other sixteen.

Usage:
    repowise mcp --transport stdio  # for Claude Code / Cursor / Cline
    repowise mcp --transport streamable-http  # for HTTP clients
    repowise mcp --transport sse    # for legacy SSE clients
"""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Iterable
from typing import Any

# ``_state`` is the one submodule that stays eager: every other module in this
# package reads its globals, and it defers its own sqlalchemy import so the
# whole package now costs about what a bare interpreter does.
from repowise.server.mcp_server import _checkout, _state

# ---------------------------------------------------------------------------
# Lazy attribute surface (PEP 562).
#
# This package used to import all 17 tool modules — and through them
# core.analysis.health, core.generation.onboarding, FastMCP, LanceDB's
# dependency chain — at package import. That made importing any ONE tool cost
# ~2.5s, roughly 9x the entire CLI, because a leaf import initialises the
# parent package first. ``repowise.cli.main`` solved the same shape for its
# ~35 command modules with lazy registration; this is that fix for the tool
# surface, so a CLI command adapting a single MCP tool pays for that tool only.
#
# Every name below resolves on first attribute access and is then cached on the
# module, so ``from repowise.server.mcp_server import get_answer`` reads exactly
# as it did before.
# ---------------------------------------------------------------------------

#: ``tool name -> submodule``. The full set is what ``ensure_full_surface``
#: imports; individually they are what a single-tool consumer pays for.
_TOOL_MODULES: dict[str, str] = {
    "generate_refactoring_code": "tool_refactoring",
    "set_finding_status": "tool_findings",
    "get_answer": "tool_answer",
    "get_architecture": "tool_architecture",
    "get_blast_radius": "tool_blast_radius",
    "get_change_risk": "tool_change_risk",
    "get_conformance": "tool_conformance",
    "get_context": "tool_context",
    "get_dead_code": "tool_dead_code",
    "get_dependency_path": "tool_dependency",
    "get_execution_flows": "tool_flows",
    "get_health": "tool_health",
    "get_overview": "tool_overview",
    "get_risk": "tool_risk",
    "get_symbol": "tool_symbol",
    "get_why": "tool_why",
    "list_repos": "tool_repos",
    "search_codebase": "tool_search",
}

#: ``exported name -> (submodule, name within it)`` for the non-tool re-exports.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "_build_origin_story": ("_helpers", "_build_origin_story"),
    "_build_visual_context": ("_graph_utils", "build_visual_context"),
    "_compute_alignment": ("_helpers", "_compute_alignment"),
    "_get_repo": ("_helpers", "_get_repo"),
    "_is_path": ("_query_shape", "_is_path"),
    "create_mcp_server": ("_server", "create_mcp_server"),
    "run_mcp": ("_server", "run_mcp"),
}

_surface_applied = False


def tool_middleware(fn: Any) -> Any:
    """Compose the layers wrapped around every registered MCP tool.

    Each layer is signature-preserving, so the tool schemas are unchanged.
    Ordering, innermost first:

    1. ``shield`` — no exception may escape to FastMCP as a protocol-level
       isError (an early isError teaches the agent to abandon the server for
       the whole session), so it must see the raw tool.
    2. ``trust`` — adds the final transport trust envelope.
    3. ``quantize`` — rounds every float in the response. Outside the shield so
       shaped error responses are covered too.
    4. ``budget`` — caps the delivered shape. Also reports the raw tool output
       size to the interaction: this is the only layer that sees it before
       anything has been shed.
    5. ``instrument`` — derives the counterfactual and adds savings metadata.
       It must be here rather than further out, because the estimators read
       fields a later budget pass is free to drop.
    6. ``timed`` — stamps ``_meta.timing_ms`` for any tool that did not.
       On a lean envelope it removes the field instead (see ``_meta.full_meta``).
    7. ``budget`` — accounts for those final middleware fields and rechecks.
    8. ``lean`` — drops what an agent cannot act on (``_meta.lean_response``).
       Outside telemetry, which still reads the flags it removes.
    9. ``record`` — writes the one savings event for the call.

    Layer 9 is outside everything for a reason. The ledger row used to be
    written at layer 5, after which ``timed`` stamped ``_meta`` and layer 7 ran
    the budgeter twice more, free to shed content and re-stamp sizes. The
    recorded delivered size was therefore one the agent never received. Each
    layer now reports what only it can see, and the event is written when the
    payload is final.

    Named rather than inlined at the ``apply`` call so tests can wrap a tool in
    the real composition; ``tests/unit/server/mcp/test_number_precision.py``
    relies on that to prove no raw double reaches an agent.
    """
    import inspect
    import time
    from functools import wraps

    from repowise.server.mcp_server._budget import (
        enforce_response_budget,
        resolve_response_budget_repo_root,
    )
    from repowise.server.mcp_server._failure_shield import shield
    from repowise.server.mcp_server._meta import (
        drop_false_status_flags,
        finalize_trust_envelope,
        full_meta,
        lean_response,
    )
    from repowise.server.mcp_server._rounding import quantize
    from repowise.server.mcp_server._savings import event as savings_event
    from repowise.server.mcp_server._savings import instrument
    from repowise.server.mcp_server._savings import interaction as savings_interaction
    from repowise.server.mcp_server._why_deliveries import record_why_delivery

    evidence_kind = getattr(fn, "__repowise_trust_kind__", None)
    signature = inspect.signature(fn)

    def trust(inner: Any) -> Any:
        @wraps(inner)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            return finalize_trust_envelope(
                await inner(*args, **kwargs), evidence_kind=evidence_kind
            )

        return wrapped

    def timed(inner: Any) -> Any:
        """Stamp elapsed time for the tools that do not thread it themselves.

        A tool that already reports ``timing_ms`` keeps its own number, which
        measures its retrieval rather than the middleware around it. On a lean
        envelope the field is a diagnostic, so it is removed instead.
        """

        @wraps(inner)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            result = await inner(*args, **kwargs)
            if isinstance(result, dict):
                meta = result.setdefault("_meta", {})
                if isinstance(meta, dict):
                    if not full_meta(fn.__name__):
                        meta.pop("timing_ms", None)
                    elif meta.get("timing_ms") is None:
                        meta["timing_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return result

        return wrapped

    def lean_form(result: Any) -> Any:
        if not full_meta(fn.__name__):
            return lean_response(result)
        # A full-meta tool keeps its diagnostics; debug mode keeps everything.
        return result if full_meta() else drop_false_status_flags(result)

    def delivered_form(result: Any) -> Any:
        """What the passes after the budget leave of *result*: envelope, then lean."""
        return lean_form(finalize_trust_envelope(result, evidence_kind=evidence_kind))

    def budget(inner: Any) -> Any:
        @wraps(inner)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            repo_root = await resolve_response_budget_repo_root(signature, args, kwargs)
            raw = await inner(*args, **kwargs)
            live = savings_interaction.current()
            if live is not None:
                # Both instantiations run this closure, and the inner one runs
                # first, so only its value has seen untrimmed output. Guarded
                # rather than merely ignored: the measurement serializes the
                # whole payload, and the outer layer's result is discarded.
                if live.pre_budget_input_tokens is None:
                    live.observe_pre_budget(
                        savings_event.raw_response_tokens(raw, shape=delivered_form)
                    )
                live.repo_root = live.repo_root or (str(repo_root) if repo_root else None)
            result = enforce_response_budget(
                fn.__name__,
                raw,
                signature=signature,
                args=args,
                kwargs=kwargs,
                repo_root=repo_root,
            )
            result = finalize_trust_envelope(result, evidence_kind=evidence_kind)
            return enforce_response_budget(
                fn.__name__,
                result,
                signature=signature,
                args=args,
                kwargs=kwargs,
                repo_root=repo_root,
            )

        return wrapped

    def lean(inner: Any) -> Any:
        @wraps(inner)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            return lean_form(await inner(*args, **kwargs))

        return wrapped

    def record(inner: Any) -> Any:
        """Open the interaction, then write its one event once nothing can change."""

        @wraps(inner)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            token = _checkout.open_call()
            try:
                with savings_interaction.begin(fn.__name__) as live:
                    result = await inner(*args, **kwargs)
                    savings_event.record(live, result)
                    record_why_delivery(fn.__name__, live, result, kwargs)
                    return result
            finally:
                _checkout.close_call(token)

        return wrapped

    return record(lean(budget(timed(instrument(budget(quantize(trust(shield(fn)))))))))


#: Restores FastMCP's indented text block, for a client that turns out to need it.
PRETTY_JSON_ENV = "REPOWISE_MCP_PRETTY_JSON"


def wire_result(payload: Any) -> Any:
    """*payload* as FastMCP would serve it, with the text block compact.

    FastMCP renders the text with ``indent=2`` beside a structured copy that is
    already compact. The same ``fallback=str`` keeps a value JSON cannot hold
    rendering as before, and the structured copy is converted exactly as
    FastMCP converts it. ``REPOWISE_MCP_PRETTY_JSON=1`` indents the text again.
    Anything but a dict is left to FastMCP's own conversion.
    """
    import os

    if not isinstance(payload, dict):
        return payload
    import pydantic_core
    from mcp.types import CallToolResult, TextContent

    pretty = os.environ.get(PRETTY_JSON_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    text = pydantic_core.to_json(payload, fallback=str, indent=2 if pretty else None).decode()
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"result": pydantic_core.to_jsonable_python(payload)},
    )


def registered_tool(fn: Any) -> Any:
    """What the server registers: :func:`tool_middleware` plus the wire text.

    Kept out of ``tool_middleware`` because tests and the CLI await the
    middleware-wrapped functions directly and expect the dict back.
    """
    from functools import wraps

    inner = tool_middleware(fn)

    @wraps(inner)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        return wire_result(await inner(*args, **kwargs))

    return wrapped


#: Parameters a tool still accepts but does not advertise: aliases and knobs
#: agents do not reach for, and no skill, prompt or tool reply tells them to
#: pass. Every name costs schema bytes on each turn of a host that re-sends
#: the tool list. docs/agent/MCP_TOOLS.md still documents them.
_UNADVERTISED_PARAMS: dict[str, frozenset[str]] = {
    "get_context": frozenset({"compact"}),
    # get_symbol's ``id`` stays listed: get_answer hints say ``get_symbol id='...'``.
    "get_symbol": frozenset({"reference", "context_lines"}),
    "get_why": frozenset({"reference"}),
    "search_codebase": frozenset({"page_type", "pattern"}),
}


def _args_block(lines: list[str], start: int) -> tuple[dict[str, list[str]], int]:
    """The ``Args:`` entries after line *start*, by parameter, and where they end.

    ``a, b: text`` documents both names; a deeper-indented line continues the
    entry above it.
    """
    import re

    docs: dict[str, list[str]] = {}
    text: list[str] = []
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
        entry = re.match(r"    (\w+(?:, \w+)*): (.*)", lines[end])
        if entry:
            text = [entry[2].strip()]
            docs.update(dict.fromkeys(entry[1].split(", "), text))
        elif lines[end].strip():
            text.append(lines[end].strip())
        end += 1
    return docs, end


def _lift_args(description: str, properties: dict[str, Any]) -> str:
    """*description* without its ``Args:`` block, each entry moved onto its property.

    The block restates the schema in prose; on the property the text sits where
    a host shows it, once. An entry for a parameter the schema does not list is
    dropped.
    """
    import inspect

    lines = inspect.cleandoc(description).split("\n")
    if "Args:" not in lines:
        return description
    start = lines.index("Args:")
    docs, end = _args_block(lines, start)
    for name, text in docs.items():
        if name in properties:
            properties[name].setdefault("description", " ".join(text))
    return "\n".join(lines[:start] + lines[end:]).strip()


def _collapse_optional(prop: dict[str, Any]) -> None:
    """``anyOf: [T, null]`` as plain ``T``: the parameter's absence from
    ``required`` already says it is optional."""
    variants = prop.get("anyOf")
    if not isinstance(variants, list) or {"type": "null"} not in variants:
        return
    rest = [v for v in variants if v != {"type": "null"}]
    if len(rest) == 1:
        del prop["anyOf"]
        prop.update(rest[0])
    else:
        prop["anyOf"] = rest


def _slim_input_schemas(server: Any) -> None:
    """Slim every tool registered on *server* (see :func:`_slim_tool`)."""
    for tool in server._tool_manager._tools.values():
        _slim_tool(tool)


def _slim_tool(tool: Any) -> None:
    """Advertise *tool*'s schema without what restates it.

    Drops pydantic's ``title`` keys, ``default: null`` and the null branch of
    an optional parameter, unlists :data:`_UNADVERTISED_PARAMS`, and moves a
    core tool's ``Args:`` block onto its properties. Hosts re-send the tool
    list on every turn. Arguments are still validated against the function
    signature, not this dict, so an unlisted parameter keeps working.

    Also drops the output schema FastMCP derives from the return annotation:
    every tool's says only "an object under ``result``". Without one the spec
    makes ``structuredContent`` optional, and :func:`wire_result` still sends it.
    """
    from repowise.core.registry.tool_selection import CORE_TOOLS

    tool.fn_metadata.output_schema = None
    params = tool.parameters
    params.pop("title", None)
    properties = params.get("properties", {})
    for name in _UNADVERTISED_PARAMS.get(tool.name, ()):
        properties.pop(name, None)
    for prop in properties.values():
        prop.pop("title", None)
        if "default" in prop and prop["default"] is None:
            del prop["default"]
        _collapse_optional(prop)
    # Only the core profile's docstrings are written one entry per
    # parameter; other tools pack several into one entry, so lifting
    # theirs would hang one entry's text on the wrong parameter.
    if tool.description and tool.name in CORE_TOOLS:
        tool.description = _lift_args(tool.description, properties)


def _annotations(safety: str) -> Any:
    """The ``ToolAnnotations`` a tool of this safety kind is listed with.

    The mcp SDK lives above packages/core, so the registry stores plain hint
    dicts and this surface materialises them.
    """
    from mcp.types import ToolAnnotations

    from repowise.core.registry.mcp_tool_registry import SAFETY_TO_ANNOTATION_HINTS

    return ToolAnnotations(**SAFETY_TO_ANNOTATION_HINTS[safety])


def _register_tools() -> None:
    """Import every tool module and load plugins, so the registry holds every tool.

    Imported in :data:`_TOOL_MODULES` order, which is the order the server
    lists tools in.
    """
    for name, module in _TOOL_MODULES.items():
        try:
            # Read the name, not just the module: a tool package re-exports its
            # tool lazily, and that read is what imports and registers it.
            getattr(importlib.import_module(f"{__name__}.{module}"), name)
        except ImportError as exc:
            # One tool's optional dependency must not take the whole surface
            # down: a host respawns a server that dies at import, forever.
            logging.getLogger("repowise.mcp").warning(
                "repowise MCP: skipping tool module %s, cannot import %s: %s",
                module,
                exc.name or "a dependency",
                exc,
            )

    from repowise.core.registry.plugins import load_plugins

    load_plugins()  # plugin tools register before the surface is attached


def tool_definitions(names: Iterable[str]) -> list[dict[str, Any]]:
    """The ``tools/list`` entries for *names*, byte for byte as the server lists them.

    Built from the tool registry with the server's own wrapping and schema
    slimming, without creating or starting a server. Sorted by name, as the
    server lists them; a name with no registered tool is skipped. The
    ``mcp.always_load`` hint is a per-repository setting, so it is not here;
    a server that honours it adds it per session (``mark_always_loaded``).
    """
    from mcp.server.fastmcp.tools import Tool
    from mcp.types import Tool as MCPTool

    from repowise.core.registry import mcp_tool_registry

    _register_tools()
    wanted = set(names)
    tools: dict[str, Any] = {}
    for entry in mcp_tool_registry.entries():
        # The server keeps the first tool registered under a name.
        if entry.name in wanted and entry.name not in tools:
            tool = Tool.from_function(
                registered_tool(entry.fn), annotations=_annotations(entry.safety)
            )
            _slim_tool(tool)
            tools[entry.name] = tool
    return [
        MCPTool(
            name=tool.name,
            title=tool.title,
            description=tool.description,
            inputSchema=tool.parameters,
            outputSchema=tool.output_schema,
            annotations=tool.annotations,
            icons=tool.icons,
            _meta=tool.meta,
        ).model_dump(by_alias=True, mode="json", exclude_none=True)
        for _, tool in sorted(tools.items())
    ]


def ensure_full_surface() -> Any:
    """Import every tool module and attach the registry to the FastMCP server.

    This is the work that used to happen at package import. It is what makes
    ``mcp`` a *populated* server rather than a bare instance, so every caller
    that reads the advertised tool set has to go through here first:
    ``create_mcp_server`` / ``run_mcp``, the ``mcp`` attribute itself, and the
    selection layer (``apply_tool_selection`` / ``describe_tool_surface``,
    which the dashboard's ``/mcp`` routes import directly).

    Idempotent, and returns the server instance so callers need not re-import
    it. Note that ``mcp_tool_registry.apply`` is itself a no-op on a repeat
    call for the same server.
    """
    global _surface_applied
    from repowise.server.mcp_server._server import mcp as _mcp

    if _surface_applied:
        return _mcp

    _register_tools()

    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server._tool_selection import snapshot_full_surface

    mcp_tool_registry.apply(_mcp, middleware=registered_tool, annotations_for=_annotations)
    _slim_input_schemas(_mcp)

    # Snapshot the full registered surface so per-server tool selection
    # (single-repo vs workspace, config/CLI overrides) can rebuild from it.
    # Selection itself runs later, once the repo path is known.
    snapshot_full_surface(_mcp)

    _surface_applied = True
    return _mcp


# ---------------------------------------------------------------------------
# Backward-compatible access to _state globals.
#
# Test fixtures and some internal code do:
#     import repowise.server.mcp_server as mcp_mod
#     mcp_mod._session_factory = factory        # write
#     await mcp_mod._vector_store.search(...)    # read
#
# We proxy reads via module __getattr__ (PEP 562) and writes via a custom
# module __class__ override so that all mutations go to _state.
# ---------------------------------------------------------------------------

_STATE_NAMES = frozenset(
    {
        "_session_factory",
        "_vector_store",
        "_decision_store",
        "_fts",
        "_repo_path",
        "_served_repo_id",
        "_vector_store_ready",
        "_lancedb_ready",
        "_registry",
        "_workspace_root",
        "_cross_repo_enricher",
        "_embedder_status",
        "_release_check",
        "_release_announced",
    }
)


def __getattr__(name: str) -> Any:
    # State names proxy through to _state on every read — they are mutable and
    # must never be cached onto this module.
    if name in _STATE_NAMES:
        return getattr(_state, name)

    if name == "mcp":
        # Reading ``mcp`` means reading the advertised tool set, so the surface
        # has to exist by the time it is handed over.
        value: Any = ensure_full_surface()
    elif name in _TOOL_MODULES:
        value = getattr(importlib.import_module(f"{__name__}.{_TOOL_MODULES[name]}"), name)
    elif name in _LAZY_ATTRS:
        module_name, attr = _LAZY_ATTRS[name]
        value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attr)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:
    """List the lazy names too, which ``__getattr__`` alone does not.

    PEP 562 pairs the two for exactly this reason: without it a name resolves
    fine through ``getattr`` but is invisible to ``dir()``, REPL completion, and
    any ``name in dir(module)`` check.
    """
    return sorted(
        set(globals()) | set(_STATE_NAMES) | set(_TOOL_MODULES) | set(_LAZY_ATTRS) | {"mcp"}
    )


_Module = type(sys.modules[__name__])


class _WritableModule(_Module):
    def __setattr__(self, name: str, value: Any) -> None:
        if name in _STATE_NAMES:
            setattr(_state, name, value)
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _WritableModule

__all__ = [
    "_build_origin_story",
    "_build_visual_context",
    "_compute_alignment",
    "_get_repo",
    "_is_path",
    "create_mcp_server",
    "ensure_full_surface",
    "generate_refactoring_code",
    "get_answer",
    "get_architecture",
    "get_blast_radius",
    "get_change_risk",
    "get_conformance",
    "get_context",
    "get_dead_code",
    "get_dependency_path",
    "get_execution_flows",
    "get_health",
    "get_overview",
    "get_risk",
    "get_symbol",
    "get_why",
    "list_repos",
    "mcp",
    "run_mcp",
    "search_codebase",
    "tool_definitions",
]
