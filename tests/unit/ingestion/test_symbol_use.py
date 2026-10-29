"""How much production code reaches a symbol: the signal spotlights rank by."""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.graph import (
    NO_SYMBOL_USE,
    SymbolUse,
    is_test_caller,
    symbol_use,
    symbol_use_counts,
    symbol_use_edges,
)

TARGET = "lib/store.py::Store"


def _graph() -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_node("tests/test_store.py", node_type="file", is_test=True)
    g.add_node("lib/store.py", node_type="file")
    for node, path, kind in (
        (TARGET, "lib/store.py", "class"),
        ("lib/store.py::helper", "lib/store.py", "function"),
        ("lib/store.py::__module__", "lib/store.py", "module"),
        ("app/main.py::run", "app/main.py", "function"),
        ("app/boot.py::__module__", "app/boot.py", "module"),
        ("tests/test_store.py::test_put", "tests/test_store.py", "function"),
    ):
        g.add_node(node, node_type="symbol", kind=kind, name=node.rsplit("::", 1)[1], file_path=path)
    return g


def test_a_test_file_caller_counts_toward_neither_total():
    g = _graph()
    g.add_edge("app/main.py::run", TARGET, edge_type="calls")
    g.add_edge("tests/test_store.py::test_put", TARGET, edge_type="calls")

    assert symbol_use(g, TARGET) == SymbolUse(callers=1, cross_file=1)
    test_src = g.nodes["tests/test_store.py::test_put"]
    assert is_test_caller(g, test_src)
    assert not is_test_caller(g, g.nodes["app/main.py::run"])


def test_the_defining_files_top_level_is_skipped_and_anothers_counts():
    g = _graph()
    g.add_edge("lib/store.py::__module__", TARGET, edge_type="references")
    g.add_edge("app/boot.py::__module__", TARGET, edge_type="calls")

    sources = [src for src, _, _ in symbol_use_edges(g, TARGET)]

    assert sources == ["app/boot.py::__module__"]
    assert symbol_use(g, TARGET) == SymbolUse(callers=1, cross_file=1)


def test_same_file_callers_count_but_not_as_cross_file():
    g = _graph()
    g.add_edge("lib/store.py::helper", TARGET, edge_type="calls")
    assert symbol_use(g, TARGET) == SymbolUse(callers=1, cross_file=0)


def test_non_use_edges_and_file_sources_are_not_uses():
    g = _graph()
    g.add_edge("lib/store.py", TARGET, edge_type="defines")
    g.add_edge("app/main.py::run", TARGET, edge_type="co_changes")
    assert symbol_use_edges(g, TARGET) == []
    assert symbol_use(g, TARGET) is NO_SYMBOL_USE


def test_no_in_edges_or_unknown_node_gives_no_use():
    g = _graph()
    assert symbol_use(g, TARGET) is NO_SYMBOL_USE
    assert symbol_use(g, "missing.py::x") is NO_SYMBOL_USE
    assert symbol_use_edges(g, "missing.py::x") == []


def test_counts_cover_only_used_symbols():
    g = _graph()
    g.add_edge("app/main.py::run", TARGET, edge_type="calls")
    g.add_edge("app/main.py::run", "lib/store.py::helper", edge_type="calls")
    g.add_edge("tests/test_store.py::test_put", "lib/store.py::helper", edge_type="calls")

    assert symbol_use_counts(g) == {
        TARGET: SymbolUse(1, 1),
        "lib/store.py::helper": SymbolUse(1, 1),
    }


def test_the_builder_reads_uses_off_its_whole_graph():
    from types import SimpleNamespace

    from repowise.core.ingestion.graph._metrics import MetricsMixin

    g = _graph()
    g.add_edge("app/main.py::run", TARGET, edge_type="calls")
    builder = SimpleNamespace(graph=lambda: g)
    assert MetricsMixin.symbol_uses(builder) == {TARGET: SymbolUse(1, 1)}
