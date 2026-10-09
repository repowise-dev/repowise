"""Callers one hop past a forwarding wrapper.

A thin wrapper (a runtime seam, a re-export, a lazy-load shim) sits between a
symbol and the code that really uses it, so the symbol's direct callers name
the wrapper and stop. This module recognises such wrappers from the graph
alone and returns *their* callers, each marked with the wrapper it came
through. It is one bounded hop over wrappers only, never a general walk.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import get_graph_nodes_by_ids
from repowise.core.persistence.models import GraphEdge, GraphNode

#: Same floor as the direct caller rows, so a hop row is no weaker than they are.
MIN_CALL_CONFIDENCE = 0.7

#: Line span of a function whose only resolved call is the target. A pass-through
#: is 3 lines, 5 with a wrapped signature; on a 97k-function TypeScript index
#: single-callee functions spread to 40+ lines, and the longer ones do work the
#: graph does not see.
_MAX_FORWARDER_SPAN = 6

#: A same-named function in another file is a re-export-style wrapper even when
#: it adds a guard or a log line the graph does not see. It must still call
#: nothing but the target. Measured median 5 lines, p75 9, p90 18.
_MAX_SAME_NAME_SPAN = 15

#: Extra rows per target. Wrapper callers are a hint about where to look next,
#: and the wrapper itself is already listed for the agent to expand.
MAX_WRAPPER_CALLER_ROWS = 10

_WRAPPER_KINDS = ("function", "method")


def _span(node: GraphNode) -> int | None:
    if node.start_line is None or node.end_line is None:
        return None
    return node.end_line - node.start_line + 1


def _is_wrapper(node: GraphNode, target: GraphNode, callees: set[str]) -> bool:
    if node.node_type != "symbol" or node.kind not in _WRAPPER_KINDS:
        return False
    span = _span(node)
    # A caller that resolves any other call does work of its own.
    if span is None or callees != {target.node_id}:
        return False
    if span <= _MAX_FORWARDER_SPAN:
        return True
    return (
        node.name == target.name
        and node.file_path != target.file_path
        and span <= _MAX_SAME_NAME_SPAN
    )


def _imported_names(edge: GraphEdge) -> list[str]:
    try:
        names = json.loads(edge.imported_names_json or "[]")
    except (TypeError, ValueError):
        return []
    return [str(n) for n in names] if isinstance(names, list) else []


async def forwarding_wrapper_callers(
    session: AsyncSession,
    repo_id: str,
    target: GraphNode,
    caller_ids: list[str],
    *,
    limit: int = MAX_WRAPPER_CALLER_ROWS,
    known_nodes: dict[str, GraphNode] | None = None,
) -> list[dict[str, Any]]:
    """Rows for the callers of every forwarding wrapper among ``caller_ids``.

    ``caller_ids`` are the target's direct callers, best first; wrappers are
    expanded in that order. A row has the direct-caller shape plus
    ``via_wrapper`` (the wrapper's symbol id). A wrapper no call edge reaches
    falls back to the files importing it by name or wholesale, shaped like a
    file-level caller row (``imports: true``): a dynamic import consumed in a
    callback is the usual reason the call itself went unresolved.
    ``known_nodes`` saves the caller-node read when the caller already holds them.
    """
    if not caller_ids or limit <= 0:
        return []
    candidates = known_nodes
    if candidates is None:
        candidates = await get_graph_nodes_by_ids(session, repo_id, caller_ids)
    small = [
        cid
        for cid in caller_ids
        if (n := candidates.get(cid)) is not None
        and n.kind in _WRAPPER_KINDS
        and (_span(n) or _MAX_SAME_NAME_SPAN + 1) <= _MAX_SAME_NAME_SPAN
    ]
    if not small:
        return []

    callees: dict[str, set[str]] = {cid: set() for cid in small}
    res = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.source_node_id.in_(small),
            GraphEdge.edge_type == "calls",
        )
    )
    for src, dst in res.all():
        callees[src].add(dst)
    wrappers = [cid for cid in small if _is_wrapper(candidates[cid], target, callees[cid])]
    if not wrappers:
        return []

    res = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_(wrappers),
            GraphEdge.edge_type == "calls",
            GraphEdge.confidence >= MIN_CALL_CONFIDENCE,
        )
    )
    inbound: dict[str, list[GraphEdge]] = {w: [] for w in wrappers}
    for edge in res.scalars().all():
        inbound[edge.target_node_id].append(edge)

    # The target, its direct callers and the wrappers are already accounted
    # for; skipping them is also what keeps a wrapper cycle from re-entering.
    seen = {target.node_id, *caller_ids}
    picked: list[tuple[GraphEdge, str]] = []
    unreached: list[str] = []
    for wrapper_id in wrappers:
        edges = sorted(inbound[wrapper_id], key=lambda e: (-(e.confidence or 0), e.source_node_id))
        if not edges:
            unreached.append(wrapper_id)
        for edge in edges:
            if edge.source_node_id in seen:
                continue
            seen.add(edge.source_node_id)
            picked.append((edge, wrapper_id))

    nodes = await get_graph_nodes_by_ids(session, repo_id, [e.source_node_id for e, _ in picked])
    rows: list[dict[str, Any]] = []
    for edge, wrapper_id in picked[:limit]:
        other = nodes.get(edge.source_node_id)
        row: dict[str, Any] = {
            "symbol_id": edge.source_node_id,
            "name": other.name if other else edge.source_node_id.split("::")[-1],
            "kind": other.kind if other else None,
            "file": other.file_path if other else edge.source_node_id.split("::")[0],
            "line": other.start_line if other else None,
            "confidence": edge.confidence,
            "edge_type": edge.edge_type,
        }
        if edge.resolution_origin:
            row["via"] = edge.resolution_origin
        row["via_wrapper"] = wrapper_id
        rows.append(row)

    if unreached and len(rows) < limit:
        seen_files = {sid.split("::")[0] for sid in seen}
        imported = await files_importing(
            session, repo_id, [candidates[w] for w in unreached], seen_files
        )
        for importer, confidence, wrapper_id, named in imported[: limit - len(rows)]:
            row = {"file": importer, "imports": True, "confidence": confidence}
            # A wholesale (``*``) import names no symbol: the file is a lead, not a proven caller.
            if not named:
                row["wholesale"] = True
            row["via_wrapper"] = wrapper_id
            rows.append(row)
    return rows


async def files_importing(
    session: AsyncSession,
    repo_id: str,
    symbols: list[GraphNode],
    skip_files: set[str],
) -> list[tuple[str, float | None, str, bool]]:
    """``(importer, confidence, symbol_id, named)`` for files importing a symbol.

    ``named`` is False when the import is wholesale (``*``), which says only
    that the file loads the module, not that it uses this symbol.

    One row per importing file, outside ``skip_files``, in a stable order. The
    file-level edge is all the graph has when the symbol is passed by
    reference or loaded dynamically rather than called.
    """
    by_file: dict[str, list[GraphNode]] = {}
    for node in symbols:
        by_file.setdefault(node.file_path, []).append(node)
    if not by_file:
        return []
    res = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_(list(by_file)),
            GraphEdge.edge_type == "imports",
        )
    )
    seen = set(skip_files)
    out: list[tuple[str, float | None, str, bool]] = []
    for edge in sorted(res.scalars().all(), key=lambda e: (e.target_node_id, e.source_node_id)):
        importer = edge.source_node_id
        if importer in seen:
            continue
        names = _imported_names(edge)
        candidates = by_file[edge.target_node_id]
        node = next((n for n in candidates if n.name in names), None)
        named = node is not None
        if node is None and "*" in names:
            node = candidates[0]
        if node is None:
            continue
        seen.add(importer)
        out.append((importer, edge.confidence, node.node_id, named))
    return out
