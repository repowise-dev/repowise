"""How much production code reaches a symbol, read off the dependency graph.

Spotlight selection, spotlight pages and file pages all count uses the same
way, so it lives here, in a module that imports no graph-building code: MCP
read paths reach it and must stay light.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .models import SYMBOL_USE_EDGE_TYPES


class SymbolUse(NamedTuple):
    """How much production code reaches one symbol.

    ``callers`` counts distinct symbols with a use edge (a call, heritage or
    dispatch link) into it; ``cross_file`` is the subset declared in another
    file. Callers in test files are left out of both: a test exercising a
    helper says nothing about how much the system leans on it.
    """

    callers: int = 0
    cross_file: int = 0


NO_SYMBOL_USE = SymbolUse()


def symbol_use_edges(g: Any, node: str) -> list[tuple[str, dict, dict]]:
    """``(source, source_attrs, edge_attrs)`` for each symbol that uses *node*.

    A use is an edge of :data:`SYMBOL_USE_EDGE_TYPES` from another symbol. The
    defining file's own top-level code is left out: there a mention is the
    export or registration line, not a use.
    """
    if node not in g:
        return []
    home = g.nodes[node].get("file_path", "")
    out: list[tuple[str, dict, dict]] = []
    for src, _, data in g.in_edges(node, data=True):
        if data.get("edge_type") not in SYMBOL_USE_EDGE_TYPES:
            continue
        src_data = g.nodes[src]
        if src_data.get("node_type") != "symbol":
            continue
        if src_data.get("kind") == "module" and src_data.get("file_path", "") == home:
            continue
        out.append((src, src_data, data))
    return out


def is_test_caller(g: Any, src_data: dict) -> bool:
    """Whether a using symbol lives in a test file (the file node's stored flag)."""
    return bool(g.nodes.get(src_data.get("file_path", ""), {}).get("is_test"))


def symbol_use(g: Any, node: str) -> SymbolUse:
    """Production callers of one symbol node of *g*."""
    home = g.nodes[node].get("file_path", "") if node in g else ""
    callers = cross = 0
    for _, src_data, _ in symbol_use_edges(g, node):
        if is_test_caller(g, src_data):
            continue
        callers += 1
        cross += src_data.get("file_path", "") != home
    return SymbolUse(callers, cross) if callers else NO_SYMBOL_USE


def symbol_use_counts(g: Any) -> dict[str, SymbolUse]:
    """Production callers per symbol node of *g*; symbols nobody uses are absent."""
    out: dict[str, SymbolUse] = {}
    for node, data in g.nodes(data=True):
        if data.get("node_type") == "symbol":
            use = symbol_use(g, node)
            if use.callers:
                out[node] = use
    return out
