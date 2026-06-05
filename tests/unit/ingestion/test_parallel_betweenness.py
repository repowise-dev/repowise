"""Equivalence tests for the parallel exact betweenness kernel.

The parallel path must reproduce ``nx.betweenness_centrality`` exactly
(modulo float summation order, bounded well below 1e-9 relative).
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.ingestion.graph import _betweenness as bw


def _random_digraph(n: int, p: float, seed: int) -> nx.DiGraph:
    g = nx.gnp_random_graph(n, p, seed=seed, directed=True)
    # Relabel to strings to mirror real node ids (file paths / symbol ids).
    return nx.relabel_nodes(g, {i: f"pkg/mod_{i}.py::sym_{i}" for i in g.nodes()})


def _assert_close(ours: dict, theirs: dict) -> None:
    assert set(ours) == set(theirs)
    for node, expected in theirs.items():
        assert ours[node] == pytest.approx(expected, rel=1e-9, abs=1e-12), node


class TestSequentialFallback:
    """Below the cost threshold the call must route to NetworkX directly."""

    def test_small_graph_equals_networkx(self):
        g = _random_digraph(60, 0.08, seed=1)
        _assert_close(
            bw.betweenness_centrality_fast(g, normalized=True),
            nx.betweenness_centrality(g, normalized=True),
        )

    def test_empty_graph(self):
        assert bw.betweenness_centrality_fast(nx.DiGraph()) == {}

    def test_single_worker_stays_sequential(self, monkeypatch):
        monkeypatch.setattr(bw, "_PARALLEL_COST_THRESHOLD", 0)
        g = _random_digraph(40, 0.1, seed=2)
        _assert_close(
            bw.betweenness_centrality_fast(g, max_workers=1),
            nx.betweenness_centrality(g, normalized=True),
        )


class TestParallelPath:
    """Force the pool on small graphs and compare against NetworkX."""

    @pytest.fixture()
    def force_parallel(self, monkeypatch):
        monkeypatch.setattr(bw, "_PARALLEL_COST_THRESHOLD", 0)

    @pytest.mark.parametrize("seed", [3, 4])
    def test_directed_equivalence(self, force_parallel, seed):
        g = _random_digraph(80, 0.06, seed=seed)
        ours = bw.betweenness_centrality_fast(g, normalized=True, max_workers=2)
        _assert_close(ours, nx.betweenness_centrality(g, normalized=True))

    def test_unnormalized_equivalence(self, force_parallel):
        g = _random_digraph(70, 0.07, seed=5)
        ours = bw.betweenness_centrality_fast(g, normalized=False, max_workers=2)
        _assert_close(ours, nx.betweenness_centrality(g, normalized=False))

    def test_disconnected_components_and_isolates(self, force_parallel):
        g = _random_digraph(50, 0.05, seed=6)
        g.add_nodes_from(["lonely.py", "isolated.py::sym"])
        ours = bw.betweenness_centrality_fast(g, normalized=True, max_workers=2)
        _assert_close(ours, nx.betweenness_centrality(g, normalized=True))
        assert ours["lonely.py"] == 0.0


class TestRescale:
    """The inlined rescale must match NetworkX for our (k=None, no-endpoint) use."""

    @pytest.mark.parametrize("normalized", [True, False])
    @pytest.mark.parametrize("directed", [True, False])
    def test_matches_networkx_rescale(self, normalized, directed):
        from networkx.algorithms.centrality.betweenness import _rescale as nx_rescale

        raw = {0: 4.0, 1: 0.0, 2: 7.5, 3: 1.25}
        ours = bw._rescale(dict(raw), 10, normalized=normalized, directed=directed)
        theirs = nx_rescale(
            dict(raw), 10, normalized=normalized, directed=directed, endpoints=False
        )
        assert ours == theirs

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_degenerate_sizes_no_scale(self, n):
        raw = {0: 3.0}
        assert bw._rescale(dict(raw), n, normalized=True, directed=True) == raw
