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
    _symbol(graph, "c.py::load", "c.py", "load", 1, 5)
    graph.add_edge("a.py::owner", "b.py::load", edge_type="calls")
    graph.add_edge("a.py::owner", "c.py::load", edge_type="dispatches_to")

    assert ExecutionGraphIndex(graph).resolve_call_targets("a.py::owner", 4, "load") == (
        ("b.py::load",),
        "name-fallback",
    )


def test_refreshed_caller_does_not_fallback_for_an_unresolved_call_site():
    graph = nx.DiGraph()
    _symbol(graph, "a.py::owner", "a.py", "owner", 1, 8)
    _symbol(graph, "db.py::load", "db.py", "load", 1, 5)
    # db.load() resolved outside the loop; obj.load() at line 6 did not resolve.
    graph.add_edge("a.py::owner", "db.py::load", edge_type="calls", call_lines=[3])

    assert ExecutionGraphIndex(graph).resolve_call_targets("a.py::owner", 6, "load") == (
        (),
        "call-site",
    )


def test_affected_files_covers_changed_caller_and_changed_sink_in_one_bounded_walk():
    graph = nx.DiGraph()
    for path, name in (("caller.py", "run"), ("helper.py", "load"), ("sink.py", "fetch")):
        sid = f"{path}::{name}"
        _symbol(graph, sid, path, name, 1, 10)
        graph.add_edge(path, sid, edge_type="defines")
    graph.add_edge("caller.py::run", "helper.py::load", edge_type="calls", confidence=0.9)
    graph.add_edge("helper.py::load", "sink.py::fetch", edge_type="dispatches_to", confidence=0.9)
    index = ExecutionGraphIndex(graph)

    assert index.affected_files({"caller.py"}) == {"caller.py", "helper.py", "sink.py"}
    assert index.affected_files({"sink.py"}) == {"caller.py", "helper.py", "sink.py"}


def test_affected_files_does_not_cross_unreliable_edges():
    graph = nx.DiGraph()
    for path, name in (("caller.py", "run"), ("sink.py", "fetch")):
        sid = f"{path}::{name}"
        _symbol(graph, sid, path, name, 1, 10)
        graph.add_edge(path, sid, edge_type="defines")
    graph.add_edge(
        "caller.py::run",
        "sink.py::fetch",
        edge_type="calls",
        confidence=0.9,
        resolution_origin="global_unique",
    )
    assert ExecutionGraphIndex(graph).affected_files({"sink.py"}) == {"sink.py"}


def test_affected_files_changed_caller_includes_siblings_of_reached_sink():
    graph = nx.DiGraph()
    for path in ("a.py", "b.py", "sink.py"):
        sid = f"{path}::run"
        _symbol(graph, sid, path, "run", 1, 10)
        graph.add_edge(path, sid, edge_type="defines")
    graph.add_edge("a.py::run", "sink.py::run", edge_type="calls", confidence=0.9)
    graph.add_edge("b.py::run", "sink.py::run", edge_type="calls", confidence=0.9)

    assert ExecutionGraphIndex(graph).affected_files({"a.py"}) == {
        "a.py",
        "b.py",
        "sink.py",
    }
