"""The System Map's lean view of ``system_graph.json``.

The map draws nodes and edges. The stored graph also carries the extraction
diagnostics (most of its bytes on a real estate) and up to
:data:`~repowise.core.workspace.system_graph.MAX_EDGE_REFS` evidence refs on
every edge, which only a drawer showing one edge reads. This projection drops
both so the map's first request carries only what it draws; the diagnostics
are served on their own by the diagnostics route.

Pure: one parsed ``system_graph.json`` dict in, the wire dict out, so any
server holding the artifact serves the same view.
"""

from __future__ import annotations

from typing import Any

from repowise.core.workspace.system_graph import SystemEdge, SystemNode

__all__ = ["lean_system_graph"]

#: Top-level keys the map never reads. Every other key passes through, so a
#: field added to the graph reaches the map without a change here.
_DROPPED_KEYS = frozenset({"diagnostics", "repo_provenance"})


def _lean_edge(raw: dict[str, Any]) -> dict[str, Any]:
    edge = {**raw, **SystemEdge.from_dict(raw).to_dict()}
    refs = edge.pop("contract_refs") or []
    edge["ref_count"] = len(set(refs))
    # A co-change edge's refs are its file pairs, which nothing else serves
    # per service pair, so the drawer keeps them (bounded by MAX_EDGE_REFS). A
    # structural edge's evidence is its contract links, read from the
    # contracts routes when the edge is opened.
    if not edge["structural"]:
        edge["contract_refs"] = refs
    return edge


def lean_system_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """The graph without diagnostics or structural edge refs, plus ``ref_count``.

    Nodes and edges are normalized the way the stored-graph reader does, so
    an artifact written before a field existed reads with its default; keys the
    dataclasses do not know (a node's ``group``, say) pass through untouched.
    ``ref_count`` is the number of distinct refs the full edge keeps, so the
    drawer can state how much evidence there is before fetching it.
    """
    lean = {k: v for k, v in graph.items() if k not in _DROPPED_KEYS}
    lean.setdefault("version", 1)
    lean.setdefault("generated_at", "")
    lean["nodes"] = [{**n, **SystemNode.from_dict(n).to_dict()} for n in graph.get("nodes", [])]
    lean["edges"] = [_lean_edge(e) for e in graph.get("edges", [])]
    return lean
