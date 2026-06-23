"""Structure-keyed betweenness cache: reuse iff the subgraph is unchanged.

A content edit that doesn't move call/heritage/import edges must reuse the
previous run's betweenness values exactly; any structural change must
recompute. No cache dir -> behavior (and filesystem) unchanged.
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


def test_signature_mismatch_returns_none(tmp_path):
    cache = CentralityCache(tmp_path)
    cache.put("symbol", "sig-1", {"n": 0.5})
    assert cache.get("symbol", "sig-1") == {"n": 0.5}
    assert cache.get("symbol", "sig-2") is None
    assert cache.get("file", "sig-1") is None

    # A fresh instance reads back from disk.
    cache2 = CentralityCache(tmp_path)
    assert cache2.get("symbol", "sig-1") == {"n": 0.5}


async def test_compute_metrics_parallel_with_cache(tmp_path):
    """Both kinds computed concurrently must land in one cache file."""
    gb = _build(tmp_path, _FILES)
    await gb.compute_metrics_parallel()

    cache = CentralityCache(tmp_path)
    file_sig = subgraph_signature(gb.file_subgraph())
    sym_sig = subgraph_signature(gb.symbol_subgraph())
    assert cache.get("file", file_sig) == gb.betweenness_centrality()
    assert cache.get("symbol", sym_sig) == gb.symbol_betweenness_centrality()


def test_centrality_cache_is_picklable(tmp_path):
    """The cache's ``threading.Lock`` must not block pickling (it's dropped
    and recreated), and entries survive the round trip."""
    import pickle

    cache = CentralityCache(tmp_path)
    cache.put("file", "sig-1", {"n": 0.25})

    restored = pickle.loads(pickle.dumps(cache))
    assert restored.get("file", "sig-1") == {"n": 0.25}
    # The lock is recreated (not None) so the restored cache is usable.
    assert restored.get("file", "sig-2") is None


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
