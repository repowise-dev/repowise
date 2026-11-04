"""Import edges read from the AST graph: imports only, and partial on failure."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx

from repowise.core.analysis.kg_inputs import _file_import_edges, import_edges_of


def _graph() -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_edge("a.py", "b.py", edge_type="imports")
    g.add_edge("a.py", "a.py::f", edge_type="contains")
    g.add_edge("b.py", "c.py")  # untyped edges are imports
    g.add_edge("c.py", "a.py", edge_type="co_change")
    return g


def test_only_import_edges_between_named_nodes():
    assert sorted(import_edges_of(_graph())) == [("a.py", "b.py"), ("b.py", "c.py")]


class _BreaksAfterOne:
    def edges(self, data=False):
        yield ("a.py", "b.py", {"edge_type": "imports"})
        raise RuntimeError("graph mutated during iteration")


def test_a_failure_part_way_keeps_the_edges_read_so_far():
    assert import_edges_of(_BreaksAfterOne()) == [("a.py", "b.py")]


def test_a_builder_whose_graph_fails_gives_no_edges():
    def boom():
        raise RuntimeError("no graph")

    assert _file_import_edges(SimpleNamespace(graph=boom)) == []
    assert _file_import_edges(SimpleNamespace(graph=_graph)) == import_edges_of(_graph())
