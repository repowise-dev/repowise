"""Inbound users of the file an impact answer leads with.

Impact questions are mostly phrased by behaviour, so no named symbol resolves
and ``graph_callers`` stays empty. The file the answer leads with still has
users in the graph: callers of its symbols (and one hop past forwarding
wrappers), files importing it, and value references. They are served as
``graph_neighbors``, one row per user file, production first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import get_graph_nodes_by_ids
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.test_paths import is_test_path
from repowise.server.mcp_server._budget import cap_collection
from repowise.server.mcp_server._edit_sites import attach_call_text, first_call_line, import_sites
from repowise.server.mcp_server._helpers import filter_dicts_by_key
from repowise.server.mcp_server._wrapper_callers import (
    MIN_CALL_CONFIDENCE,
    forwarding_wrapper_callers,
    imported_names,
)
from repowise.server.mcp_server.tool_answer.callers import caller_row, short_id

#: Kinds of use, strongest first; a file using the lead several ways keeps its strongest row.
_USE_RANK = {"calls": 0, "dynamic_imports": 1, "references": 2, "imports": 3}
#: Inbound symbol edges read for one file, best confidence first, so a hub stays bounded.
_MAX_EDGES = 200
#: Question-relevant symbols read on their own first, so a hub's popular symbols
#: cannot crowd them out of the capped read.
_MAX_RELEVANT = 2
_MAX_RELEVANT_EDGES = 50
#: Symbols whose forwarding wrappers are followed one hop.
_MAX_FOCUS = 3
_MAX_ROWS = 8


def lead_file(payload: dict) -> str | None:
    """The file the answer names first: its first citation, else the top guess or fallback."""
    for path in payload.get("citations") or []:
        if isinstance(path, str) and path:
            return path
    for guess in payload.get("best_guesses") or []:
        if isinstance(guess, dict) and guess.get("file"):
            return guess["file"]
    targets = payload.get("fallback_targets") or []
    return targets[0] if targets and isinstance(targets[0], str) else None


def _relevance(hits: list[dict], lead: str) -> dict[str, float]:
    """Symbol name -> how many of the question's terms retrieval found in it."""
    hit = next((h for h in hits if h.get("target_path") == lead), {})
    return {s.get("name"): s.get("_relevance") or 0 for s in hit.get("symbols") or []}


def _focus(symbols: dict[str, GraphNode], edges: list[GraphEdge], relevance: dict[str, float]):
    """The lead's symbols ranked by the question's terms, then by how many other files call them."""
    callers: dict[str, set[str]] = {}
    for e in edges:
        if e.edge_type == "calls":
            callers.setdefault(e.target_node_id, set()).add(e.source_node_id.split("::")[0])
    ranked = sorted(
        callers,
        key=lambda sid: (-relevance.get(symbols[sid].name, 0), -len(callers[sid]), sid),
    )
    return {sid: i for i, sid in enumerate(ranked)}


async def _inbound(
    session: AsyncSession, repo_id: str, targets: list[str], limit: int
) -> list[GraphEdge]:
    """Calls and value references into *targets*, best confidence first."""
    if not targets:
        return []
    res = await session.execute(
        select(GraphEdge)
        .where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id.in_(targets),
            GraphEdge.edge_type.in_(("calls", "references")),
            GraphEdge.confidence >= MIN_CALL_CONFIDENCE,
        )
        .order_by(GraphEdge.confidence.desc(), GraphEdge.source_node_id)
        .limit(limit)
    )
    return list(res.scalars().all())


async def neighbor_evidence(
    session: AsyncSession,
    repo_id: str,
    lead: str | None,
    hits: list[dict],
    exclude_spec: Any = None,
    repo_root: str | Path | None = None,
) -> list[dict]:
    """Rows for the files that call, import, load or reference *lead*, production first."""
    if not lead or is_test_path(lead):
        return []
    res = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == lead,
        )
    )
    symbols = {n.node_id: n for n in res.scalars().all()}
    relevance = _relevance(hits, lead)
    relevant = sorted(
        (sid for sid, n in symbols.items() if relevance.get(n.name, 0) > 0),
        key=lambda sid: (-relevance[symbols[sid].name], sid),
    )[:_MAX_RELEVANT]
    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for targets, limit in ((relevant, _MAX_RELEVANT_EDGES), (list(symbols), _MAX_EDGES)):
        for e in await _inbound(session, repo_id, targets, limit):
            key = (e.source_node_id, e.target_node_id, e.edge_type)
            if key not in seen_edges and e.source_node_id.split("::")[0] != lead:
                seen_edges.add(key)
                edges.append(e)
    res = await session.execute(
        select(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.target_node_id == lead,
            GraphEdge.edge_type.in_(("imports", "dynamic_imports")),
        )
    )
    file_edges = sorted(res.scalars().all(), key=lambda e: e.source_node_id)
    focus = _focus(symbols, edges, relevance)
    nodes = await get_graph_nodes_by_ids(session, repo_id, list({e.source_node_id for e in edges}))

    candidates: list[dict] = []
    for e in edges:
        src = nodes.get(e.source_node_id)
        candidates.append(
            caller_row(
                e.source_node_id,
                e.source_node_id.split("::")[0],
                src.start_line if src else None,
                e.target_node_id,
                e.edge_type,
                first_call_line(e.call_lines_json),
            )
        )
    for target_id in sorted(focus, key=focus.get)[:_MAX_FOCUS]:
        direct = [e.source_node_id for e in edges if e.target_node_id == target_id]
        for h in await forwarding_wrapper_callers(
            session, repo_id, symbols[target_id], direct, known_nodes=nodes
        ):
            if h["file"] == lead:
                continue
            row = caller_row(
                h.get("symbol_id") or h["file"],
                h["file"],
                h.get("line"),
                target_id,
                h.get("edge_type") or "imports",
                h.get("call_line"),
            )
            if h.get("wholesale"):
                row["wholesale"] = True
            row["via_wrapper"] = h["via_wrapper"]
            candidates.append(row)
    by_name = {n.name: n.node_id for n in symbols.values()}
    for e in file_edges:
        names = imported_names(e)
        named = next((by_name[n] for n in names if n in by_name), None)
        row = caller_row(e.source_node_id, e.source_node_id, None, named or lead, e.edge_type)
        if e.edge_type == "imports" and named is None and "*" in names:
            row["wholesale"] = True
        candidates.append(row)

    for row in candidates:
        if is_test_path(row["file"]):
            row["test"] = True
    candidates = filter_dicts_by_key(candidates, "file", exclude_spec)

    def _rank(row: dict) -> tuple:
        return (
            bool(row.get("test")),
            _USE_RANK.get(row["edge_type"], len(_USE_RANK)),
            "via_wrapper" in row,
            bool(row.get("wholesale")),
            focus.get(row["target"], len(focus)),
        )

    rows: list[dict] = []
    seen: set[str] = set()
    for row in sorted(candidates, key=_rank):
        if row["file"] not in seen:
            seen.add(row["file"])
            rows.append(row)
    served = rows[:_MAX_ROWS]
    await attach_call_text(repo_root, served)
    # Unserved rows are never checked against the live file.
    for row in rows[_MAX_ROWS:]:
        row.pop("call_line", None)
    loaders = [r for r in served if r["edge_type"] in ("imports", "dynamic_imports")]
    sites = await import_sites(repo_root, [r["file"] for r in loaders], lead)
    for row in loaders:
        if site := sites.get(row["file"]):
            row["call_line"], row["text"], dynamic = site
            if dynamic:
                row["edge_type"] = "dynamic_imports"
                row.pop("wholesale", None)
    # A load found to be dynamic outranks a plain import within the served cut.
    rows[:_MAX_ROWS] = sorted(served, key=_rank)
    return rows


def _use(row: dict) -> str:
    where = row["file"]
    if line := row.get("call_line") or row.get("line"):
        where = f"{where}:{line}"
    symbol = short_id(row["target"]) if "::" in row["target"] else "it"
    kind = row["edge_type"]
    if kind == "calls":
        via = f" via wrapper {short_id(row['via_wrapper'])}" if row.get("via_wrapper") else ""
        return f"{where} calls {symbol}{via}"
    if kind == "dynamic_imports":
        return f"{where} loads it with a dynamic import"
    if kind == "references":
        return f"{where} references {symbol}"
    return f"{where} imports {'it wholesale' if row.get('wholesale') else symbol}"


def attach_graph_neighbors(payload: dict, rows: list[dict], lead: str | None) -> dict:
    """Add ``graph_neighbors`` (shared count fields when cut) and the keyless clause naming two users."""
    if rows and lead:
        cap_collection(payload, "graph_neighbors", rows, _MAX_ROWS)
        production = [r for r in rows if not r.get("test")][:2]
        if production:
            payload["_graph_neighbors_answer"] = (
                f"From the graph, {lead} is used by: " + "; ".join(map(_use, production)) + "."
            )
    return payload
