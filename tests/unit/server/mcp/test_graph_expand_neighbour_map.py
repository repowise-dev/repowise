"""Graph expansion reads a per-index neighbour map, not every edge per question."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._answer_pipeline import graph_expand
from repowise.server.mcp_server._graph_files import file_ext

_PAIRS = [
    ("a.py", "b.py"),
    ("b.py", "a.py"),
    ("a.py", "c.py"),
    ("c.py", "d.py"),
    ("d.py", "d.py"),  # a file-layer self-loop passes the projection
    ("e.py", "a.py"),
]


def _walk_every_edge(pairs, seeds, existing):
    """The per-question walk the neighbour map replaces."""
    neighbours, degree = set(), {}
    for src, tgt in pairs:
        degree[src] = degree.get(src, 0) + 1
        degree[tgt] = degree.get(tgt, 0) + 1
        for mine, theirs in ((src, tgt), (tgt, src)):
            if mine in seeds and theirs not in existing:
                neighbours.add(theirs)
    return neighbours, degree


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seeds, existing",
    [({"a.py"}, {"a.py"}), ({"a.py", "d.py"}, {"a.py", "d.py", "c.py"}), ({"d.py"}, set())],
)
async def test_the_neighbour_map_answers_as_the_edge_walk_did(monkeypatch, seeds, existing):
    async def pairs(_session, _repo_id):
        return list(_PAIRS)

    monkeypatch.setattr(graph_expand, "_projected_edges", pairs)
    graph = await graph_expand._projected_graph(None, "r")

    neighbours, degree = _walk_every_edge(_PAIRS, seeds, existing)
    assert graph_expand._seed_neighbors(graph, seeds, existing) == neighbours
    assert graph.degree == degree


def test_file_ext_is_memoized_and_unchanged():
    assert file_ext("pkg/retrieval.PY") == "py"
    assert file_ext("Makefile") == ""
    assert file_ext("a.b/c") == ""
    hits = file_ext.cache_info().hits
    assert file_ext("pkg/retrieval.PY") == "py"
    assert file_ext.cache_info().hits == hits + 1
