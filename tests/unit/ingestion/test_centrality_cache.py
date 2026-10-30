"""Structure-keyed betweenness cache: reuse while the subgraph is close enough.

A content edit that doesn't move call/heritage/import edges must reuse the
previous run's betweenness values exactly. A structural change reuses only
while the graph has drifted no further than the churn budget, and recomputes
past it. No cache dir -> behavior (and filesystem) unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.ingestion import ASTParser, GraphBuilder
from repowise.core.ingestion.graph._centrality_cache import (
    CentralityCache,
    subgraph_signature,
)
from repowise.core.ingestion.models import FileInfo

_MAIN = "from util import helper\n\n\ndef main():\n    return helper(2)\n"
_UTIL = "def helper(x):\n    return x + 1\n"


def _parse(path: str, source: str):
    fi = FileInfo(
        path=path,
        abs_path=f"C:/fake/{path}",
        language="python",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(fi, source.encode())


def _build(cache_dir, files):
    gb = GraphBuilder("C:/fake", centrality_cache_dir=cache_dir)
    for path, source in files:
        gb.add_file(_parse(path, source))
    gb.build()
    return gb


_FILES = [("main.py", _MAIN), ("util.py", _UTIL)]


def _graph(edges):
    import networkx as nx

    g = nx.DiGraph()
    g.add_edges_from(edges)
    return g


def _lookup(cache, kind, graph, signature, max_churn=0):
    return cache.lookup(kind, graph, signature=signature, max_churn=max_churn)


def test_unchanged_structure_reuses_values(tmp_path, monkeypatch):
    gb1 = _build(tmp_path, _FILES)
    file_bc = gb1.betweenness_centrality()
    sym_bc = gb1.symbol_betweenness_centrality()
    assert (tmp_path / "centrality_cache.pkl").exists()

    def _boom(*args, **kwargs):
        raise AssertionError("betweenness must not recompute on an unchanged graph")

    monkeypatch.setattr(
        "repowise.core.ingestion.graph._betweenness.betweenness_centrality_fast", _boom
    )
    gb2 = _build(tmp_path, _FILES)
    assert gb2.betweenness_centrality() == file_bc
    assert gb2.symbol_betweenness_centrality() == sym_bc


def test_structural_change_recomputes_and_matches_fresh(tmp_path):
    _build(tmp_path, _FILES).symbol_betweenness_centrality()

    edited = _MAIN + "\n\ndef extra():\n    return main()\n"
    cached_run = _build(tmp_path, [("main.py", edited), ("util.py", _UTIL)])
    fresh_run = _build(None, [("main.py", edited), ("util.py", _UTIL)])

    assert cached_run.symbol_betweenness_centrality() == fresh_run.symbol_betweenness_centrality()
    assert cached_run.betweenness_centrality() == fresh_run.betweenness_centrality()


def test_no_cache_dir_writes_nothing(tmp_path):
    gb = _build(None, _FILES)
    gb.betweenness_centrality()
    gb.symbol_betweenness_centrality()
    assert not (tmp_path / "centrality_cache.pkl").exists()


def test_corrupt_cache_recomputes(tmp_path):
    _build(tmp_path, _FILES).symbol_betweenness_centrality()
    (tmp_path / "centrality_cache.pkl").write_bytes(b"\x00garbage")

    cached_run = _build(tmp_path, _FILES)
    fresh_run = _build(None, _FILES)
    assert cached_run.symbol_betweenness_centrality() == fresh_run.symbol_betweenness_centrality()


def test_signature_is_structure_only():
    import networkx as nx

    g1 = nx.DiGraph()
    g1.add_edge("a", "b")
    g1.add_node("c")

    g2 = nx.DiGraph()  # same structure, different insertion order + attrs
    g2.add_node("c")
    g2.add_edge("a", "b", edge_type="calls")
    g2.nodes["a"]["node_type"] = "symbol"

    assert subgraph_signature(g1) == subgraph_signature(g2)

    g2.add_edge("b", "c")
    assert subgraph_signature(g1) != subgraph_signature(g2)


def test_signature_mismatch_outside_budget_returns_none(tmp_path):
    g = _graph([("a", "b")])
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", {"a": 0.5}, graph=g, scored_commit="c0ffee")

    assert _lookup(cache, "symbol", g, "sig-1").values == {"a": 0.5}
    # A different signature on a graph nothing else matches is out of budget.
    assert _lookup(cache, "symbol", _graph([("x", "y")]), "sig-2", max_churn=0) is None
    assert _lookup(cache, "file", g, "sig-1") is None

    # A fresh instance reads back from disk, commit included.
    hit = _lookup(CentralityCache(tmp_path), "symbol", g, "sig-1")
    assert hit.values == {"a": 0.5}
    assert hit.scored_commit == "c0ffee"
    assert hit.churn == 0


def test_lookup_reuses_within_churn_budget_and_reports_drift(tmp_path):
    """One added node plus its edge is 2 churn: served, and said to be stale."""
    scored = _graph([("a", "b")])
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", {"a": 0.5, "b": 0.0}, graph=scored, scored_commit="c0ffee")

    drifted = _graph([("a", "b"), ("b", "c")])
    hit = _lookup(cache, "symbol", drifted, "sig-2", max_churn=4)
    assert hit is not None
    assert hit.values == {"a": 0.5, "b": 0.0}  # values are the scoring's, unchanged
    assert hit.churn == 2
    assert hit.scored_commit == "c0ffee"
    # "c" is absent, which is how the caller knows it was never scored.
    assert "c" not in hit.values


def test_lookup_past_churn_budget_forces_recompute(tmp_path):
    scored = _graph([("a", "b")])
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", {"a": 0.5, "b": 0.0}, graph=scored, scored_commit=None)

    drifted = _graph([("a", "b"), ("b", "c")])
    assert _lookup(cache, "symbol", drifted, "sig-2", max_churn=1) is None


def test_churn_counts_a_rewire_that_preserves_node_and_edge_counts(tmp_path):
    """Same node count, same edge count, different edges: still churn."""
    scored = _graph([("a", "b"), ("c", "d")])
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", dict.fromkeys("abcd", 0.0), graph=scored, scored_commit=None)

    rewired = _graph([("a", "d"), ("c", "b")])
    assert _lookup(cache, "symbol", rewired, "sig-2", max_churn=0) is None
    assert _lookup(cache, "symbol", rewired, "sig-2", max_churn=4).churn == 4


def test_stale_cache_version_degrades_to_recompute(tmp_path):
    """A v2 payload (entries were plain tuples) must miss, not raise."""
    from repowise.core.cache_seal import dump_sealed_pickle
    from repowise.core.ingestion.graph import _centrality_cache as cc

    dump_sealed_pickle(
        tmp_path / "centrality_cache.pkl",
        {"version": 2, "entries": {"symbol": ("sig-1", {"a": 0.5})}},
        domain="centrality_cache.pkl",
    )
    assert _lookup(CentralityCache(tmp_path), "symbol", _graph([("a", "b")]), "sig-1") is None
    assert cc._CACHE_VERSION == 4


async def test_compute_metrics_parallel_with_cache(tmp_path):
    """Both kinds computed concurrently must land in one cache file."""
    gb = _build(tmp_path, _FILES)
    await gb.compute_metrics_parallel()

    cache = CentralityCache(tmp_path)
    files, symbols = gb.file_subgraph(), gb.symbol_subgraph()
    file_hit = _lookup(cache, "file", files, subgraph_signature(files))
    sym_hit = _lookup(cache, "symbol", symbols, subgraph_signature(symbols))
    assert file_hit.values == gb.betweenness_centrality()
    assert sym_hit.values == gb.symbol_betweenness_centrality()


def test_centrality_cache_is_picklable(tmp_path):
    """The cache's ``threading.Lock`` must not block pickling (it's dropped
    and recreated), and entries survive the round trip."""
    import pickle

    g = _graph([("n", "m")])
    cache = CentralityCache(tmp_path)
    cache.put("file", "sig-1", {"n": 0.25}, graph=g, scored_commit="c0ffee")

    restored = pickle.loads(pickle.dumps(cache))
    assert _lookup(restored, "file", g, "sig-1").values == {"n": 0.25}
    # The lock is recreated (not None) so the restored cache is usable.
    assert _lookup(restored, "file", _graph([("q", "r")]), "sig-2", max_churn=0) is None


def test_graph_builder_round_trips_through_pickle(tmp_path):
    """GraphBuilder is pickled to hand built graph state across a process
    boundary (e.g. the hosted static-state bundle). A ``threading.Lock`` member
    used to make that raise ``TypeError: cannot pickle '_thread.lock'``; this
    locks in that a fully-built, cache-backed builder serializes and the
    reloaded object is still usable (the lock is recreated)."""
    import pickle

    gb = _build(tmp_path, _FILES)
    file_bc = gb.betweenness_centrality()
    pr = gb.pagerank()

    reloaded = pickle.loads(pickle.dumps(gb, protocol=pickle.HIGHEST_PROTOCOL))

    assert set(reloaded._parsed_files) == set(gb._parsed_files)
    assert reloaded.pagerank() == pr
    # The lock-guarded subgraph path must work after restore (lock recreated).
    assert reloaded.betweenness_centrality() == file_bc


def test_churn_budget_is_zero_where_recompute_is_already_cheap(tmp_path):
    """Below the minimum cost, trading accuracy for speed buys nothing, so
    those graphs keep exact-signature-only reuse."""
    gb = _build(tmp_path, _FILES)
    assert gb._churn_budget(gb.symbol_subgraph()) == 0
    assert gb._churn_budget(gb.file_subgraph()) == 0


def test_churn_budget_scales_with_the_graph_above_the_threshold(monkeypatch):
    from repowise.core.ingestion.graph import _metrics

    monkeypatch.setattr(_metrics, "_CHURN_BUDGET_MIN_COST", 0)
    gb = GraphBuilder("C:/fake")
    g = _graph([(f"n{i}", f"n{i + 1}") for i in range(999)])  # 1000 nodes, 999 edges
    assert gb._churn_budget(g) == int(1999 * 0.002)


def test_mid_graphs_under_the_parallel_threshold_get_a_budget():
    """A graph Brandes would still score sequentially is no longer exact-only:
    1500 x 1499 is above the minimum cost and far below the parallel one."""
    from repowise.core.ingestion.graph import _betweenness, _metrics

    gb = GraphBuilder("C:/fake")
    g = _graph([(f"n{i}", f"n{i + 1}") for i in range(1499)])
    cost = g.number_of_nodes() * g.number_of_edges()
    assert _metrics._CHURN_BUDGET_MIN_COST <= cost < _betweenness._PARALLEL_COST_THRESHOLD
    assert gb._churn_budget(g) == int(2999 * _metrics._CHURN_BUDGET_FRACTION) > 0


def test_structural_change_within_budget_skips_brandes(tmp_path, monkeypatch):
    """The phase's whole point: one added symbol must not rerun Brandes."""
    from repowise.core.ingestion.graph import _betweenness, _metrics

    monkeypatch.setattr(_metrics, "_CHURN_BUDGET_MIN_COST", 0)
    gb = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path, head_commit="c0ffee")
    big = _graph([(f"n{i}", f"n{i + 1}") for i in range(999)])
    monkeypatch.setattr(gb, "symbol_subgraph", lambda: big)
    scored = gb.symbol_betweenness_centrality()
    assert gb.betweenness_scoring("symbol").scored_commit == "c0ffee"
    assert gb.betweenness_scoring("symbol").churn == 0

    def _boom(*args, **kwargs):
        raise AssertionError("betweenness must not recompute inside the churn budget")

    monkeypatch.setattr(_betweenness, "betweenness_centrality_fast", _boom)
    gb2 = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path, head_commit="deadbee")
    drifted = big.copy()
    drifted.add_edge("n999", "n1000")
    monkeypatch.setattr(gb2, "symbol_subgraph", lambda: drifted)

    assert gb2.symbol_betweenness_centrality() == scored
    reuse = gb2.betweenness_scoring("symbol")
    # Reported as the older commit's scoring, not the commit being indexed.
    assert reuse.scored_commit == "c0ffee"
    assert reuse.churn == 2
    assert "n1000" not in reuse.values


def test_mid_graph_reuses_within_budget_and_rescores_exactly_past_it(tmp_path, monkeypatch):
    """No patched thresholds: a mid graph serves its scoring inside the budget
    and returns to exact Brandes the moment it drifts past it."""
    import networkx as nx

    from repowise.core.ingestion.graph import _metrics

    chain = _graph([(f"n{i:04d}", f"n{i + 1:04d}") for i in range(1499)])
    budget = int(2999 * _metrics._CHURN_BUDGET_FRACTION)
    gb = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path, head_commit="c0ffee")
    monkeypatch.setattr(gb, "symbol_subgraph", lambda: chain)
    scored = gb.symbol_betweenness_centrality()

    near = chain.copy()
    near.add_edge("n1499", "x0")  # churn 2
    gb2 = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path, head_commit="deadbee")
    monkeypatch.setattr(gb2, "symbol_subgraph", lambda: near)
    assert gb2.symbol_betweenness_centrality() == scored
    assert gb2.betweenness_scoring("symbol").churn == 2 <= budget

    far = chain.copy()
    far.add_edges_from((f"n{i:04d}", f"y{i}") for i in range(budget))  # churn 2 * budget
    gb3 = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path, head_commit="deadbee")
    monkeypatch.setattr(gb3, "symbol_subgraph", lambda: far)
    assert gb3.symbol_betweenness_centrality() == nx.betweenness_centrality(far)
    assert gb3.betweenness_scoring("symbol").churn == 0


def _forbid_pagerank(monkeypatch):
    import networkx as nx

    def _boom(*args, **kwargs):
        raise AssertionError("pagerank must not recompute on an unchanged graph")

    monkeypatch.setattr(nx, "pagerank", _boom)


def test_unchanged_structure_reuses_pagerank(tmp_path, monkeypatch):
    first = _build(tmp_path, _FILES)
    file_pr, sym_pr = first.pagerank(), first.symbol_pagerank()
    fresh = _build(None, _FILES)
    assert (fresh.pagerank(), fresh.symbol_pagerank()) == (file_pr, sym_pr)

    _forbid_pagerank(monkeypatch)
    reused = _build(tmp_path, _FILES)
    assert reused.pagerank() == file_pr
    assert reused.symbol_pagerank() == sym_pr


def test_reused_pagerank_is_within_the_persist_tolerance_of_a_fresh_one(tmp_path):
    """Insertion order moves PageRank only in the last bits; a reused value must
    stay inside the tolerance graph_nodes persistence already applies."""
    import networkx as nx
    import pytest

    from repowise.core.persistence.crud.graph import _CENTRALITY_ATOL

    edges = [(f"m{i % 97}", f"m{(i * 7 + 3) % 97}") for i in range(400)]
    scored = _graph(edges)
    gb = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path)
    gb.symbol_subgraph = lambda: scored
    cached = gb.symbol_pagerank()

    shuffled = _graph(list(reversed(edges)))
    reused_builder = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path)
    reused_builder.symbol_subgraph = lambda: shuffled
    reused = reused_builder.symbol_pagerank()
    assert reused == cached
    assert reused == pytest.approx(nx.pagerank(shuffled), rel=0, abs=_CENTRALITY_ATOL)


def test_structural_change_recomputes_pagerank(tmp_path):
    _build(tmp_path, _FILES).symbol_pagerank()
    edited = [("main.py", _MAIN + "\n\ndef extra():\n    return main()\n"), ("util.py", _UTIL)]
    cached_run = _build(tmp_path, edited)
    fresh_run = _build(None, edited)
    assert cached_run.symbol_pagerank() == fresh_run.symbol_pagerank()
    assert cached_run.pagerank() == fresh_run.pagerank()


def test_pagerank_cache_keys_on_alpha(tmp_path):
    cache = CentralityCache(tmp_path)
    cache.put_pagerank("file", signature="sig-1", alpha=0.85, values={"a": 1.0})
    assert cache.lookup_pagerank("file", signature="sig-1", alpha=0.85) == {"a": 1.0}
    assert cache.lookup_pagerank("file", signature="sig-1", alpha=0.5) is None
    assert cache.lookup_pagerank("file", signature="sig-2", alpha=0.85) is None
    assert cache.lookup_pagerank("symbol", signature="sig-1", alpha=0.85) is None


async def test_pagerank_and_betweenness_hash_each_subgraph_once(tmp_path, monkeypatch):
    from repowise.core.ingestion.graph import _centrality_cache

    calls = []
    real = _centrality_cache.subgraph_signature

    def counting(graph):
        calls.append(graph)
        return real(graph)

    monkeypatch.setattr(_centrality_cache, "subgraph_signature", counting)
    gb = _build(tmp_path, _FILES)
    await gb.compute_metrics_parallel()
    assert len(calls) == 2  # one file subgraph, one symbol subgraph


def test_pagerank_and_betweenness_share_one_cache_file(tmp_path):
    """Writing one kind's entry never drops the other's."""
    gb = _build(tmp_path, _FILES)
    file_bc = gb.betweenness_centrality()
    file_pr = gb.pagerank()

    reloaded = CentralityCache(tmp_path)
    files = gb.centrality_subgraph()
    signature = subgraph_signature(files)
    assert _lookup(reloaded, "file", files, signature).values == file_bc
    assert reloaded.lookup_pagerank("file", signature=signature, alpha=0.85) == file_pr


def test_cache_file_without_pagerank_still_serves_betweenness(tmp_path):
    """Files written before PageRank was cached keep their betweenness entries."""
    from repowise.core.cache_seal import dump_sealed_pickle
    from repowise.core.ingestion.graph import _centrality_cache as cc

    g = _graph([("a", "b")])
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", {"a": 0.5, "b": 0.0}, graph=g, scored_commit=None)
    dump_sealed_pickle(
        tmp_path / "centrality_cache.pkl",
        {"version": cc._CACHE_VERSION, "entries": cache._entries},
        domain="centrality_cache.pkl",
    )
    old = CentralityCache(tmp_path)
    assert _lookup(old, "symbol", g, "sig-1").values == {"a": 0.5, "b": 0.0}
    assert old.lookup_pagerank("symbol", signature="sig-1", alpha=0.85) is None


def test_weighted_graphs_are_never_served_cached_pagerank(tmp_path):
    """The signature ignores weights, so a weight-only change must not hit."""
    import networkx as nx

    g = _graph([("a", "b"), ("b", "c"), ("c", "a")])
    gb = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path)
    gb.symbol_subgraph = lambda: g
    gb.symbol_pagerank()

    weighted = g.copy()
    weighted["a"]["b"]["weight"] = 5.0
    gb2 = GraphBuilder("C:/fake", centrality_cache_dir=tmp_path)
    gb2.symbol_subgraph = lambda: weighted
    assert gb2.symbol_pagerank() == nx.pagerank(weighted)
