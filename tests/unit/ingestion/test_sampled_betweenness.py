"""The parallel sampled betweenness kernel must equal the sequential NetworkX call bit for bit."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from itertools import pairwise

import networkx as nx
import pytest

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.graph import _betweenness as bw
from repowise.core.ingestion.graph import _metrics
from repowise.core.ingestion.graph import _sampled_betweenness as sb


def _graph(n: int, p: float, seed: int, *, reverse: bool = False) -> nx.DiGraph:
    base = nx.gnp_random_graph(n, p, seed=seed, directed=True)
    ids = {i: f"pkg/mod_{i % 13}.py::sym_{i}" for i in base}
    edges = [(ids[u], ids[v]) for u, v in base.edges()]
    nodes = [ids[i] for i in base]
    g = nx.DiGraph()
    # Insertion order varies between runs in production; vary it here too.
    g.add_nodes_from(reversed(nodes) if reverse else nodes)
    g.add_edges_from(reversed(edges) if reverse else edges)
    return g


def _sequential(g: nx.DiGraph, k: int, seed: int = 42) -> dict[str, float]:
    ordered = nx.DiGraph()
    ordered.add_nodes_from(sorted(g))
    ordered.add_edges_from(g.edges())
    return nx.betweenness_centrality(ordered, k=k, normalized=True, seed=seed)


@pytest.fixture()
def force_parallel(monkeypatch):
    monkeypatch.setattr(bw, "_PARALLEL_COST_THRESHOLD", 0)


@pytest.fixture()
def pool_only(force_parallel, monkeypatch):
    """Fail instead of falling back, so equality proves the pool computed it."""

    def _no_fallback(*args):
        pytest.fail("the parallel path fell back to the sequential call")

    monkeypatch.setattr(sb, "_sequential", _no_fallback)


class TestBitIdentity:
    @pytest.mark.parametrize("reverse", [False, True], ids=["inserted", "reversed"])
    @pytest.mark.parametrize("seed", [3, 4])
    def test_equals_sequential_exactly(self, pool_only, seed, reverse):
        g = _graph(150, 0.03, seed=seed, reverse=reverse)
        ours = sb.sampled_betweenness_centrality(g, k=60, seed=42, max_workers=2)
        expected = _sequential(g, k=60)
        assert ours == expected  # exact float equality, not approx
        assert list(ours) == list(expected)  # same key order

    def test_isolates_and_disconnected_components(self, pool_only):
        g = _graph(90, 0.02, seed=6)
        g.add_nodes_from(f"unused_{i}" for i in range(40))
        assert sb.sampled_betweenness_centrality(g, k=50, seed=42, max_workers=2) == (
            _sequential(g, k=50)
        )

    def test_other_seed_matches_its_own_sequential_draw(self, pool_only):
        g = _graph(120, 0.03, seed=7)
        ours = sb.sampled_betweenness_centrality(g, k=30, seed=7, max_workers=2)
        assert ours == _sequential(g, k=30, seed=7)
        assert ours != _sequential(g, k=30, seed=42)

    def test_metrics_large_graph_branch_uses_the_pool(self, pool_only, monkeypatch):
        g = _graph(650, 0.004, seed=8)
        monkeypatch.setattr(_metrics, "_LARGE_REPO_THRESHOLD", 600)
        monkeypatch.setattr(bw, "pool_worker_count", lambda max_workers=None: 2)
        real_run = bw._run_pool_within_budget
        tasks = []

        def recording_run(**kwargs):
            tasks.append(kwargs["task"])
            return real_run(**kwargs)

        monkeypatch.setattr(bw, "_run_pool_within_budget", recording_run)
        builder = GraphBuilder()
        graph = builder.graph()
        graph.add_nodes_from(g, node_type="symbol")
        graph.add_edges_from(g.edges(), edge_type="calls")
        assert builder.symbol_betweenness_centrality() == _sequential(g, k=500)
        assert tasks == [sb._source_dependencies]

    def test_k_covering_every_node_is_left_to_networkx(self, force_parallel, monkeypatch):
        def unexpected(**kwargs):
            pytest.fail("k == n is exact betweenness, which NetworkX scores itself")

        monkeypatch.setattr(bw, "_run_pool_within_budget", unexpected)
        g = _graph(40, 0.08, seed=13)
        assert sb.sampled_betweenness_centrality(g, k=40, seed=42) == _sequential(g, k=40)


class TestFallbacks:
    def test_cheap_graphs_stay_sequential(self, monkeypatch):
        def unexpected(**kwargs):
            pytest.fail("a graph under the cost threshold must not start a pool")

        monkeypatch.setattr(bw, "_run_pool_within_budget", unexpected)
        g = _graph(80, 0.04, seed=9)
        assert sb.sampled_betweenness_centrality(g, k=20, seed=42) == _sequential(g, k=20)

    def test_failed_pool_falls_back_to_the_same_values(self, force_parallel, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("pool unavailable")

        monkeypatch.setattr(bw, "ProcessPoolExecutor", _boom)
        g = _graph(100, 0.03, seed=10)
        assert sb.sampled_betweenness_centrality(g, k=40, seed=42, max_workers=2) == (
            _sequential(g, k=40)
        )


class _InlineExecutor:
    """Runs each task on submit, slowly, so progress pacing is deterministic."""

    delay = 0.0

    def __init__(self, *, initializer, initargs, **kwargs):
        initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        bw._WORKER_GRAPH = None
        return False

    def submit(self, fn, chunk):
        time.sleep(self.delay)
        future: Future = Future()
        future.set_result(fn(chunk))
        return future


class TestProgressBudget:
    """The budget bounds the wait for the next chunk, not the whole run."""

    def test_slow_but_advancing_pool_is_not_cut_off(self, pool_only, monkeypatch):
        monkeypatch.setattr(_InlineExecutor, "delay", 0.05)
        monkeypatch.setattr(bw, "ProcessPoolExecutor", _InlineExecutor)
        g = _graph(120, 0.03, seed=11)
        # 64 sources in 1-source chunks: ~3 s of work against a 0.5 s budget.
        monkeypatch.setattr(sb, "_CHUNKS_PER_WORKER", 32)
        ours = sb.sampled_betweenness_centrality(g, k=64, seed=42, max_workers=2, timeout=0.5)
        assert ours == _sequential(g, k=64)

    def test_stalled_pool_falls_back(self, force_parallel, monkeypatch):
        started = threading.Event()

        class _Stalled:
            def __init__(self, *args, **kwargs):
                started.set()
                threading.Event().wait(30)

        monkeypatch.setattr(bw, "ProcessPoolExecutor", _Stalled)
        g = _graph(100, 0.03, seed=12)
        ours = sb.sampled_betweenness_centrality(g, k=40, seed=42, max_workers=2, timeout=0.2)
        assert started.is_set()
        assert ours == _sequential(g, k=40)


def test_many_tied_shortest_paths_stay_bit_identical(pool_only):
    """A layered graph multiplies path counts (large sigma), where float
    rounding is most sensitive to accumulation order."""
    g = nx.DiGraph()
    layers = [[f"L{depth}/n{i}" for i in range(6)] for depth in range(8)]
    for upper, lower in pairwise(layers):
        g.add_edges_from((u, v) for u in upper for v in lower)
    ours = sb.sampled_betweenness_centrality(g, k=20, seed=42, max_workers=2)
    assert ours == _sequential(g, k=20)


def test_failing_chunk_cancels_queued_work(force_parallel, monkeypatch):
    """A raising task must not leave the rest of the window to run first."""
    cancelled = []

    class _Future(Future):
        def cancel(self):
            cancelled.append(self)
            return super().cancel()

    class _FailingExecutor(_InlineExecutor):
        def submit(self, fn, chunk):
            future = _Future()
            if chunk == "boom":
                future.set_exception(RuntimeError("worker died"))
            return future

    monkeypatch.setattr(bw, "ProcessPoolExecutor", _FailingExecutor)
    with pytest.raises(RuntimeError):
        bw._run_pool(
            n=1,
            edges=[],
            directed=True,
            chunks=["boom", "a", "b"],
            workers=2,
            task=lambda c: c,
            fold=lambda t, r: None,
        )
    assert len(cancelled) == 2
