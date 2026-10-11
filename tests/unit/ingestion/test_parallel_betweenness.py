"""Equivalence tests for the parallel exact betweenness kernel.

The parallel path must reproduce ``nx.betweenness_centrality`` exactly
(modulo float summation order, bounded well below 1e-9 relative).
"""

from __future__ import annotations

import threading

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


class TestWorkerCap:
    """The pool size is bounded regardless of how many cores the host has.

    Every worker costs one serial spawn plus one full copy of the edge list
    written down its pipe, so an uncapped count is what turned a high-core
    host into a 32-spawn serial startup that stalled partway through.
    """

    def test_cap_applies_to_host_core_count(self, monkeypatch):
        monkeypatch.setattr(bw.os, "cpu_count", lambda: 32)
        assert bw.pool_worker_count() == bw._MAX_POOL_WORKERS

    def test_cap_applies_to_explicit_request(self):
        assert bw.pool_worker_count(64) == bw._MAX_POOL_WORKERS

    def test_below_cap_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(bw.os, "cpu_count", lambda: 4)
        assert bw.pool_worker_count() == 4
        assert bw.pool_worker_count(2) == 2

    def test_unknown_core_count_degrades_to_one(self, monkeypatch):
        monkeypatch.setattr(bw.os, "cpu_count", lambda: None)
        assert bw.pool_worker_count() == 1

    def test_pool_is_constructed_with_the_capped_count(self, monkeypatch):
        """The cap reaches the executor, not just the helper."""
        monkeypatch.setattr(bw, "_PARALLEL_COST_THRESHOLD", 0)
        monkeypatch.setattr(bw.os, "cpu_count", lambda: 32)
        seen: dict = {}

        real_executor = bw.ProcessPoolExecutor

        def _recording_executor(*args, **kwargs):
            seen.update(kwargs)
            # Honour the cap under test but keep the pool small enough that the
            # assertion does not cost 8 interpreters to make.
            kwargs["max_workers"] = 2
            return real_executor(*args, **kwargs)

        monkeypatch.setattr(bw, "ProcessPoolExecutor", _recording_executor)
        bw.betweenness_centrality_fast(_random_digraph(40, 0.1, seed=7))

        assert seen["max_workers"] == bw._MAX_POOL_WORKERS


class TestTimeoutFallback:
    """A pool that stops making progress must fall back, not hang.

    The failure this closes raises nothing: the parent parks inside a blocking
    pipe write while spawning a worker, so the ``except`` around the pool never
    fires and the update hangs forever holding its lock. Only a wall-clock
    budget can turn that into a fallback.
    """

    @pytest.fixture()
    def force_parallel(self, monkeypatch):
        monkeypatch.setattr(bw, "_PARALLEL_COST_THRESHOLD", 0)

    def test_stalled_pool_falls_back_to_sequential(self, force_parallel, monkeypatch):
        """A pool construction that never returns yields the sequential answer.

        The stall is injected and the budget is injected, so the test proves the
        fallback without waiting out a real timeout.
        """
        started = threading.Event()

        class _StalledExecutor:
            def __init__(self, *args, **kwargs):
                started.set()
                # Never returns within the injected budget. Daemon-threaded by
                # the code under test, so it cannot outlive the interpreter.
                threading.Event().wait(30)

        monkeypatch.setattr(bw, "ProcessPoolExecutor", _StalledExecutor)

        g = _random_digraph(60, 0.08, seed=8)
        ours = bw.betweenness_centrality_fast(g, normalized=True, timeout=0.2)

        assert started.is_set(), "the parallel path was never attempted"
        _assert_close(ours, nx.betweenness_centrality(g, normalized=True))

    def test_raising_pool_still_falls_back(self, force_parallel, monkeypatch):
        """The pre-existing exception path survives the move onto a thread."""

        def _boom(*args, **kwargs):
            raise OSError("pool unavailable")

        monkeypatch.setattr(bw, "ProcessPoolExecutor", _boom)

        g = _random_digraph(60, 0.08, seed=9)
        _assert_close(
            bw.betweenness_centrality_fast(g, normalized=True),
            nx.betweenness_centrality(g, normalized=True),
        )

    def test_budget_not_consumed_by_a_healthy_pool(self, force_parallel):
        """A pool that completes inside the budget returns the parallel result."""
        g = _random_digraph(80, 0.06, seed=10)
        ours = bw.betweenness_centrality_fast(g, normalized=True, max_workers=2, timeout=120)
        _assert_close(ours, nx.betweenness_centrality(g, normalized=True))


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
