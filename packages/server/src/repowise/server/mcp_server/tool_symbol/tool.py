"""MCP Tool: get_symbol — byte-precise source retrieval for a single symbol."""

from __future__ import annotations

import time
from typing import Any

from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._references import (
    omission_reference,
)
from repowise.server.mcp_server._workspace_routing import (
    hint_misses,
    needs_routing,
    other_repo_roots,
    route_targets,
)

from .ambiguous import _render_ambiguous
from .body import _serve_symbol
from .callees import _MAX_CALLEE_DEPTH
from .lookup import _resolve_symbol, _rows_in_scope
from .misses import _not_found_response
from .omission import _extract_omission_ref, _resolve_omission_ref
from .ranges import _range_id_read
from .text import _elapsed_ms


def _reference_arguments(
    reference: dict[str, Any] | None,
    symbol_id: str | None,
    id: str | None,
    repo: str | None,
) -> tuple[str | None, str | None]:
    """``symbol_id`` and ``repo`` filled from a structured source reference."""
    if not reference:
        return symbol_id, repo
    if not symbol_id and not id and isinstance(reference.get("id"), str):
        symbol_id = reference["id"]
    if repo is None and _pkg._is_workspace_mode() and isinstance(reference.get("repository"), str):
        repo = reference["repository"]
    return symbol_id, repo


def _requested_id(
    symbol_id: str | None, id: str | None, query: str | None
) -> tuple[str | None, str | None]:
    """The id asked for, from ``symbol_id``, its ``id`` alias, or a lone ``query``."""
    # Agents call this tool as ``get_symbol(id)``; ``symbol_id`` wins when both are given.
    if not symbol_id and id:
        symbol_id = id
    elif not symbol_id and query and query.strip():
        # A lone ``query`` is a guessed id: the line filter needs an id to apply to.
        symbol_id, query = query, None
    return symbol_id, query


async def _routed_read(
    symbol_id: str, context_lines: int, query: str | None, depth: int
) -> dict | None:
    """The read served by the repo that owns *symbol_id*, or None to serve it here."""
    routes = await route_targets([symbol_id.strip()])
    if not needs_routing(routes):
        return None
    route = routes[0]
    if route.repo is None:
        return {"symbol_id": symbol_id, **route.card(), "_meta": _build_meta()}
    routed = await get_symbol(
        route.local, context_lines=context_lines, repo=route.repo, query=query, depth=depth
    )
    if route.routed:
        routed = {"repo": route.repo, **routed}
    hint_misses(routed, routes)
    return routed


async def _indexed_symbol_read(
    ctx: Any, symbol_id: str, context_lines: int, depth: int, t0: float
) -> dict:
    """Resolve *symbol_id* in the index and serve it, its candidates, or a miss."""
    context_lines = max(0, min(50, context_lines))
    # Clamped, not rejected: the deepest walk we do is the useful reply.
    depth = max(1, min(_MAX_CALLEE_DEPTH, depth))

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        rows = await _resolve_symbol(session, repository.id, symbol_id)

    # Before the exclusion spec: it is built from the repo path.
    if not ctx.path:
        return {
            "symbol_id": symbol_id,
            "error": "MCP server has no repo path configured",
            "_meta": _build_meta(
                timing_ms=_elapsed_ms(t0),
                repository=repository,
            ),
        }

    exclude_spec = _get_exclude_spec(ctx.path)
    rows = await _rows_in_scope(ctx, repository, symbol_id, rows, exclude_spec)
    if not rows:
        return await _not_found_response(ctx, repository, symbol_id, exclude_spec, t0)
    if len(rows) > 1:
        return await _render_ambiguous(rows, symbol_id, ctx, repository, t0, context_lines)
    return await _serve_symbol(
        ctx, repository, rows[0], symbol_id, context_lines, depth, exclude_spec, t0
    )


@mcp.tool(surface_order=30, artifact_type="source", presentation="source", evidence_basis="measured")
async def get_symbol(
    symbol_id: str | None = None,
    context_lines: int = 0,
    repo: str | None = None,
    query: str | None = None,
    id: str | None = None,
    depth: int = 1,
    reference: dict[str, Any] | None = None,
) -> dict:
    """Follow-up read of one symbol whose id another response already gave you.

    **Not an entry point.** ``get_answer`` already ships ``symbol_bodies``, and
    for a whole file ``get_context(include=["skeleton"])`` or a plain Read is
    one call instead of many. Reach here for a body that was elided, or for a
    ``continuation`` / omission ref. Never walk a file symbol by symbol.

    Returns verified, line-numbered source for one indexed symbol, live range,
    or omission ref. Ambiguity returns every candidate; an index miss returns
    live fallback lines. A truncated result carries the exact continuation to
    pass straight back.

    Args:
        symbol_id: "path/to/file.py::Name", "path/to/file.py:140-180" for a
            live range, or an omission ref. A bare file path is not an id.
        context_lines: extra lines before/after (0-50).
        repo: usually omitted.
        query: omission refs only, regex/substring filter on lines. With
            no id given, it is read as ``symbol_id``.
        id: accepted alias for ``symbol_id``.
        depth: 1 (default) is this symbol alone; 2-3 also returns the bodies
            it calls, transitively, in ``callee_bodies``.
        reference: structured source reference emitted by this tool. Its id
            and repository are accepted together without caller translation.
    """
    symbol_id, repo = _reference_arguments(reference, symbol_id, id, repo)
    if repo == "all":
        return _unsupported_repo_all("get_symbol")
    ctx = await _pkg._resolve_repo_context(repo)

    t0 = time.perf_counter()
    symbol_id, query = _requested_id(symbol_id, id, query)
    if not symbol_id or not symbol_id.strip():
        from repowise.server.mcp_server._savings.interaction import note_error_reason

        note_error_reason("invalid_args")
        return {
            "symbol_id": symbol_id,
            "error": 'get_symbol requires `symbol_id`, e.g. "path/to/file.py::Name".',
            "_meta": _build_meta(timing_ms=_elapsed_ms(t0)),
        }

    omission_ref = _extract_omission_ref(symbol_id)
    if omission_ref is not None:
        canonical_omission = omission_reference(symbol_id) or symbol_id
        return _resolve_omission_ref(
            canonical_omission,
            omission_ref,
            query,
            ctx.path,
            t0,
            other_repo_roots() if repo is None else (),
        )

    if repo is None:
        routed = await _routed_read(symbol_id, context_lines, query, depth)
        if routed is not None:
            return routed
    ranged = await _range_id_read(symbol_id, context_lines, ctx, t0)
    if ranged is not None:
        return ranged
    return await _indexed_symbol_read(ctx, symbol_id, context_lines, depth, t0)
