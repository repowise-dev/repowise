"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import networkx as nx


def _now() -> datetime:
    return datetime.now(UTC)


def _old_date(days: int = 365) -> datetime:
    """Return a datetime `days` ago (timezone-aware)."""
    return _now() - timedelta(days=days)


def _build_graph(
    nodes: dict[str, dict],
    edges: list[tuple[str, str]] | None = None,
) -> nx.DiGraph:
    """Create a DiGraph with the given node attributes and edges.

    ``nodes`` maps node-id to its attribute dict.  If a node has a
    ``symbols`` list, each entry is promoted to a proper symbol node
    connected to the file via a ``defines`` edge (matching the real
    GraphBuilder layout).
    ``edges`` is a list of (src, dst) pairs; edge data can be
    supplied as a 3-tuple (src, dst, data_dict).
    """
    g = nx.DiGraph()
    for name, attrs in nodes.items():
        attrs.setdefault("language", "python")
        # Extract symbols before adding the file node
        sym_list = attrs.pop("symbols", [])
        g.add_node(name, **attrs)
        # Create symbol nodes + defines edges (mirrors GraphBuilder.add_file)
        for sym in sym_list:
            sym_id = f"{name}::{sym['name']}"
            g.add_node(
                sym_id,
                node_type="symbol",
                file_path=name,
                **sym,
            )
            g.add_edge(name, sym_id, edge_type="defines")
    for edge in edges or []:
        if len(edge) == 3:
            g.add_edge(edge[0], edge[1], **(edge[2]))
        else:
            g.add_edge(edge[0], edge[1])
    return g
