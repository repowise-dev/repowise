"""The execution graph is one policy and one index for every consumer."""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.execution_graph import (
    ExecutionGraphIndex,
    is_reliable_execution_edge,
)
from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES


def _symbol(graph, node_id, path, name, start, end):
    graph.add_node(
        node_id,
        node_type="symbol",
        file_path=path,
        name=name,
        start_line=start,
        end_line=end,
    )


def test_execution_vocabulary_has_one_reliability_predicate():
    for edge_type in EXECUTION_EDGE_TYPES:
        assert is_reliable_execution_edge(edge_type, None)
        assert is_reliable_execution_edge(edge_type, "same_file")
        assert not is_reliable_execution_edge(edge_type, "global_unique")
    assert not is_reliable_execution_edge("references", None)
    assert not is_reliable_execution_edge("reads", None)
    assert not is_reliable_execution_edge("imports", None)


def test_index_builds_all_views_in_one_pass_and_deduplicates_edges():
    graph = nx.MultiDiGraph()
    _symbol(graph, "t.py::test", "t.py", "test", 2, 5)
    _symbol(graph, "a.py::base", "a.py", "base", 10, 20)
    _symbol(graph, "a.py::impl", "a.py", "impl", 22, 30)
    graph.add_edge("t.py", "t.py::test", edge_type="defines")
    graph.add_edge("t.py::test", "a.py::base", edge_type="calls", call_lines=[3])
    graph.add_edge("a.py::base", "a.py::impl", edge_type="dispatches_to")
    graph.add_edge("a.py::base", "a.py::impl", edge_type="dispatches_to")
    graph.add_edge(
        "t.py::test",
        "bad.py::guess",
        edge_type="calls",
        resolution_origin="global_unique",
    )
    graph.add_edge("t.py::test", "a.py::mentioned", edge_type="references")

    index = ExecutionGraphIndex(graph)

    assert index.declares == {"t.py": ("t.py::test",)}
    assert index.forward == {
        "t.py::test": ("a.py::base",),
        "a.py::base": ("a.py::impl",),
    }
    assert index.reverse["a.py::impl"] == ("a.py::base",)
    assert index.in_degree["a.py::impl"] == 1
    assert index.resolve_call_targets("t.py::test", 3, "base") == (
        ("a.py::base",),
        "call-site",
    )


def test_definition_lookup_prefers_exact_then_innermost_range():
    graph = nx.DiGraph()
    _symbol(graph, "a.py::outer", "a.py", "outer", 2, 20)
    _symbol(graph, "a.py::inner", "a.py", "inner", 7, 10)
    graph.add_node("a.py::__module__", node_type="symbol", name="__module__")
    index = ExecutionGraphIndex(graph)

    assert index.resolve_function("a.py", 7) == "a.py::inner"
    assert index.resolve_function("a.py", 8) == "a.py::inner"
    assert index.resolve_function("a.py", 15) == "a.py::outer"
    assert index.resolve_function("a.py", 0) == "a.py::__module__"


def test_old_index_without_call_lines_uses_labelled_name_fallback():
    graph = nx.DiGraph()
    _symbol(graph, "a.py::owner", "a.py", "owner", 1, 5)
    _symbol(graph, "b.py::load", "b.py", "load", 1, 5)
    graph.add_edge("a.py::owner", "b.py::load", edge_type="calls")

    assert ExecutionGraphIndex(graph).resolve_call_targets("a.py::owner", 4, "load") == (
        ("b.py::load",),
        "name-fallback",
    )
