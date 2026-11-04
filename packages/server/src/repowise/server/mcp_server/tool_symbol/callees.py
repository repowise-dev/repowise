"""Callee expansion: call-graph bodies served beside the root symbol."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_symbol as _pkg
from repowise.server.mcp_server._helpers import (
    clean_signature,
)
from repowise.server.mcp_server._references import (
    source_reference,
    symbol_identity,
)
from repowise.server.mcp_server._verify import (
    check_symbol_bounds,
)

from .text import _number_lines, _read_file_text, _slice_text

# Callee expansion. A symbol whose body names the next symbol you need is the
# common case, and fetching that next one is a fresh round trip: the chain gets
# walked a hop at a time, each hop a full request/response, when the graph
# already knows every edge. ``depth`` serves the reachable bodies together.
#
# Depth is capped low on purpose. Fan-out compounds, so depth 3 on a hub symbol
# is a payload nobody asked for; hand-walks are chains, not trees, and are
# covered well before the cap.
_MAX_CALLEE_DEPTH = 3
# Total chars of callee source served. Sized against the symbol cap above: a
# handful of ordinary bodies, and a hub is truncated rather than serialised.
_CALLEE_CHAR_BUDGET = 24_000
# Per-hop fan-out cap, applied before any body is read and across the whole
# frontier, not per node.
_MAX_CALLEES_PER_HOP = 12
# Callee bodies are context for the root symbol, not the subject of the call,
# so they are bounded tighter than the root's ~600.
_MAX_CALLEE_BODY_LINES = 150


class _CalleeHop(NamedTuple):
    """One hop of the walk: its true graph distance and the rows it produced.

    ``over_cap`` counts rows the per-hop fan-out cap dropped. They are reported
    as a number rather than as entries: listing them individually is the payload
    the cap exists to prevent, and saying nothing would make a silent cut out of
    a bound this module otherwise always makes recoverable. It is a floor over
    the indexed graph, not a total — the per-node edge query carries its own
    limit, so callees it never returned are not counted here either.
    """

    number: int
    rows: list[WikiSymbol]
    over_cap: int


class _CalleeBody(NamedTuple):
    """One callee's rendered body, sliced once and costed by its own length.

    ``declared_*`` are the bounds the verified row claims; ``start``/``end`` are
    what the line cap actually served, so ``end < declared_end`` is exactly the
    truncation case.
    """

    source: str
    start: int
    end: int
    declared_start: int
    declared_end: int
    verified: bool


def _slice_callee_body(row: WikiSymbol, text: str) -> _CalleeBody:
    """Slice *row* out of *text* under the callee line cap.

    Bounds are checked so ``verified`` is honest, but a correction is not
    written back here: healing belongs to the read that asked for the symbol,
    not to a neighbour swept up by a graph walk.
    """
    check = check_symbol_bounds(row, text)
    source, start, end, _total = _slice_text(
        text, check.start_line, check.end_line, 0, max_lines=_MAX_CALLEE_BODY_LINES
    )
    return _CalleeBody(
        source=_number_lines(source, start),
        start=start,
        end=end,
        declared_start=check.start_line,
        declared_end=check.end_line,
        verified=check.verified,
    )


async def _unseen_callees_of(
    session,
    repo_id: str,
    frontier: list[str],
    seen: set[str],
) -> tuple[list[str], dict[str, float]]:
    """Call targets of *frontier* not already in *seen*, with their confidence.

    Mutates *seen*, which is what keeps a diamond from being walked twice.
    Edges below the confidence floor are dropped here: tier-3 resolution
    invents edges, and a guessed one drags a whole unrelated body into the
    payload.
    """
    from repowise.core.persistence.crud import get_graph_edges_for_node
    from repowise.server.mcp_server.tool_context.enrichment import (
        _CALL_EDGE_TYPES,
        _MIN_CALL_CONFIDENCE,
    )

    next_ids: list[str] = []
    confidence_of: dict[str, float] = {}
    for node_id in frontier:
        edges = await get_graph_edges_for_node(
            session,
            repo_id,
            node_id,
            direction="callees",
            edge_types=_CALL_EDGE_TYPES,
            limit=_MAX_CALLEES_PER_HOP,
        )
        for e in edges:
            if (e.confidence or 0) < _MIN_CALL_CONFIDENCE or e.target_node_id in seen:
                continue
            seen.add(e.target_node_id)
            next_ids.append(e.target_node_id)
            confidence_of[e.target_node_id] = e.confidence or 0
    return next_ids, confidence_of


async def _discover_callee_hops(
    session,
    repo_id: str,
    root_row: WikiSymbol,
    depth: int,
    exclude_spec: Any,
) -> list[_CalleeHop]:
    """Walk the call graph and return the rows of each hop, no bodies read.

    Discovery is separate from rendering so the renderer knows, before it
    spends the first character, how many hops actually produced rows. Reserving
    a slice of the budget for a hop that turns out to be empty costs a body
    that would otherwise have been served.

    Symbol graph nodes are keyed by the same ``"{path}::{Name}"`` string as
    ``WikiSymbol.symbol_id``, so each hop is one edge query plus one row query
    regardless of fan-out. Every symbol is reached at most once, at the
    shallowest depth that reaches it, which keeps a diamond in the call graph
    from being serialised twice. A symbol past the per-hop fan-out cap is not
    re-offered at a deeper hop: it would then be labelled with a distance that
    is not its own. It is counted in ``over_cap`` instead.
    """
    seen: set[str] = {root_row.symbol_id}
    frontier = [root_row.symbol_id]
    hops: list[_CalleeHop] = []

    for hop_number in range(1, depth):
        if not frontier:
            break
        next_ids, confidence_of = await _unseen_callees_of(session, repo_id, frontier, seen)
        if not next_ids:
            break

        res = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.symbol_id.in_(next_ids),
            )
        )
        rows = [r for r in res.scalars().all() if not _pkg.is_excluded(r.file_path, exclude_spec)]
        # One hop, not one frontier node. Cut after the row lookup and the
        # exclusion filter, so a dangling edge or an excluded path cannot spend
        # a slot, and ranked by edge confidence as ``get_graph_edges_for_node``.
        rows.sort(key=lambda r: (-confidence_of.get(r.symbol_id or "", 0.0), r.symbol_id or ""))
        over_cap = max(len(rows) - _MAX_CALLEES_PER_HOP, 0)
        rows = rows[:_MAX_CALLEES_PER_HOP]
        # Stable order so the same call returns the same payload twice.
        rows.sort(key=lambda r: (r.file_path or "", r.start_line or 0, r.symbol_id or ""))
        if rows:
            # The hop number is carried, not inferred from the position in this
            # list: a hop whose rows were all excluded or all dangling would
            # otherwise renumber every hop behind it, and a grandchild would be
            # served claiming to be a direct callee.
            hops.append(_CalleeHop(number=hop_number, rows=rows, over_cap=over_cap))
        frontier = next_ids

    return hops


def _prepare_callee_entries(
    hops: list[_CalleeHop], repo_root: Path
) -> list[tuple[dict[str, Any], str, _CalleeBody | None]]:
    """One ordered record per discovered row: ``(entry, file_path, body)``.

    ``body`` is None when the file could not be read. The order is fixed here,
    so the later refill pass cannot reshuffle the payload, and each body is
    sliced exactly once, so what a body costs and what is emitted for it can
    never disagree.
    """
    text_cache: dict[str, str | None] = {}
    prepared: list[tuple[dict[str, Any], str, _CalleeBody | None]] = []
    for hop in hops:
        for row in hop.rows:
            entry: dict[str, Any] = {
                "symbol_id": symbol_identity(row.symbol_id),
                "name": row.name,
                "file": row.file_path,
                "kind": row.kind,
                "signature": clean_signature(row.signature),
                "depth": hop.number,
            }
            if row.file_path not in text_cache:
                text_cache[row.file_path] = _read_file_text(repo_root, row.file_path)
            text = text_cache[row.file_path]
            body = None if text is None else _slice_callee_body(row, text)
            prepared.append((entry, row.file_path, body))
    return prepared


def _render_callee(
    entry: dict[str, Any], file_path: str, body: _CalleeBody, repository: str
) -> None:
    """Put *body* on *entry*, with the continuation read when the line cap cut it."""
    entry.update(
        {
            "start_line": body.start,
            "end_line": body.end,
            "source": body.source,
            "verified": body.verified,
        }
    )
    if body.end < body.declared_end:
        entry["truncated"] = True
        continuation_reference = source_reference(
            repository,
            file_path,
            lines=[body.end + 1, body.declared_end],
            verification_basis="live",
            source_kind="source",
        )
        entry["continuation"] = continuation_reference["id"]
        entry["continuation_reference"] = continuation_reference


def _spend_callee_budget(
    hops: list[_CalleeHop],
    prepared: list[tuple[dict[str, Any], str, _CalleeBody | None]],
    repository: str,
) -> None:
    """Render the bodies the character budget covers, a per-hop share first."""
    remaining = _pkg._CALLEE_CHAR_BUDGET
    reserve_per_hop = _pkg._CALLEE_CHAR_BUDGET // len(hops)
    # Pass 1: every hop spends its own share and no more, including the last
    # (a cap, not a floor), so unspent budget cannot flow to deeper hops ahead
    # of a deferred direct callee. Pass 2 redistributes leftovers in hop order.
    for hop in hops:
        spendable = min(reserve_per_hop, remaining)
        for entry, file_path, body in prepared:
            if entry["depth"] != hop.number or body is None or "source" in entry:
                continue
            if len(body.source) > spendable:
                continue
            _render_callee(entry, file_path, body, repository)
            remaining -= len(body.source)
            spendable -= len(body.source)

    # Pass 2: a reserve nobody claimed goes back to whatever it deferred, in
    # hop order, so an empty deeper hop costs the caller nothing.
    for entry, file_path, body in prepared:
        if body is None or "source" in entry or len(body.source) > remaining:
            continue
        _render_callee(entry, file_path, body, repository)
        remaining -= len(body.source)


def _partition_callees(
    prepared: list[tuple[dict[str, Any], str, _CalleeBody | None]], repository: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rendered entries, and the rest with the reason or the read that fetches them."""
    entries: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for entry, file_path, body in prepared:
        if "source" in entry:
            entries.append(entry)
        elif body is None:
            entry["note"] = "source file could not be read"
            omitted.append(entry)
        else:
            # Out of budget: name the read that fetches it rather than
            # dropping the symbol silently.
            fetch_reference = source_reference(
                repository,
                file_path,
                lines=[body.declared_start, body.declared_end],
                verification_basis="live",
                source_kind="source",
            )
            entry["fetch_with"] = fetch_reference["id"]
            entry["fetch_reference"] = fetch_reference
            omitted.append(entry)
    return entries, omitted


async def _expand_callees(
    session,
    repo_id: str,
    root_row: WikiSymbol,
    repo_root: Path,
    depth: int,
    exclude_spec: Any,
    repository: str = "default",
) -> dict[str, Any] | None:
    """Breadth-first walk of the call graph from *root_row*, bodies included.

    Bodies are served against a per-hop share of the character budget rather
    than in loop order, so large direct callees cannot starve the deeper hops
    the caller asked for. The reservation is taken only across hops that
    discovery found rows for, and whatever the reserve leaves unspent is
    refilled to the bodies it deferred, in hop order, so a direct callee is
    first in line for a share the deeper hops did not use.

    Returns None when the root has no outbound call edges, so the caller adds
    no empty block to an ordinary response.
    """
    hops = await _discover_callee_hops(session, repo_id, root_row, depth, exclude_spec)
    if not hops:
        return None

    prepared = _prepare_callee_entries(hops, repo_root)
    _spend_callee_budget(hops, prepared, repository)
    entries, omitted = _partition_callees(prepared, repository)

    block: dict[str, Any] = {"depth": depth, "callees": entries}
    fan_out_capped = [
        {"depth": hop.number, "omitted": hop.over_cap} for hop in hops if hop.over_cap
    ]
    if fan_out_capped:
        block["fan_out_capped"] = fan_out_capped
    if omitted:
        block["not_rendered"] = omitted
        block["note"] = (
            "Callees past the response budget are listed in not_rendered; "
            "fetch one with its fetch_with range."
        )
    return block
