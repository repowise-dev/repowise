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
    assert cc._CACHE_VERSION == 3


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
    """Below the parallel-cost threshold, trading accuracy for speed buys
    nothing, so those graphs keep exact-signature-only reuse."""
    gb = _build(tmp_path, _FILES)
    assert gb._churn_budget(gb.symbol_subgraph()) == 0
    assert gb._churn_budget(gb.file_subgraph()) == 0


def test_churn_budget_scales_with_the_graph_above_the_threshold(monkeypatch):
    from repowise.core.ingestion.graph import _betweenness

    monkeypatch.setattr(_betweenness, "_PARALLEL_COST_THRESHOLD", 0)
    gb = GraphBuilder("C:/fake")
    g = _graph([(f"n{i}", f"n{i + 1}") for i in range(999)])  # 1000 nodes, 999 edges
    assert gb._churn_budget(g) == int(1999 * 0.002)


def test_structural_change_within_budget_skips_brandes(tmp_path, monkeypatch):
    """The phase's whole point: one added symbol must not rerun Brandes."""
    from repowise.core.ingestion.graph import _betweenness

    monkeypatch.setattr(_betweenness, "_PARALLEL_COST_THRESHOLD", 0)
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
