"""SQL-backed graph metrics: snapshot correctness + read routing.

Proves that the materialized ``graph_metrics`` snapshot reproduces the
NetworkX-computed file-level metrics, and that ``load_metrics_from_sql``
routes subsequent reads through the snapshot (so the in-memory NetworkX
object can be dropped on large repos).
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile
from repowise.core.pipeline.modes import OrchestratorMode


def _fi(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _imp(module_path: str) -> Import:
    return Import(
        raw_statement=f"import {module_path}",
        module_path=module_path,
        imported_names=[],
        is_relative=False,
        resolved_file=None,
    )


def _parsed(path: str, imports: list[Import] | None = None) -> ParsedFile:
    return ParsedFile(
        file_info=_fi(path),
        symbols=[],
        imports=imports or [],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


def _build_sample() -> GraphBuilder:
    """a imports b and c; b imports c — a small connected file graph."""
    b = GraphBuilder()
    b.add_file(_parsed("a.py", [_imp("b"), _imp("c")]))
    b.add_file(_parsed("b.py", [_imp("c")]))
    b.add_file(_parsed("c.py"))
    b.build()
    return b


def test_snapshot_matches_networkx():
    b = _build_sample()
    nx_pr = b.pagerank()
    nx_bc = b.betweenness_centrality()
    nx_cd = b.community_detection()
    nx_in = b.in_degree()
    nx_out = b.out_degree()

    snap = b.file_metrics_snapshot()

    # Every file-subgraph node is represented and matches the live NetworkX
    # values byte-for-byte.
    assert set(snap) == set(nx_pr)
    for node, m in snap.items():
        assert m["pagerank"] == nx_pr[node]
        assert m["betweenness"] == nx_bc[node]
        assert m["community_id"] == nx_cd.get(node, 0)
        assert m["in_degree"] == nx_in[node]
        assert m["out_degree"] == nx_out[node]


def test_load_from_sql_routes_reads_without_recompute():
    source = _build_sample()
    snap = source.file_metrics_snapshot()
    nx_pr = source.pagerank()

    # A fresh builder hydrated purely from the snapshot.
    hydrated = _build_sample()
    hydrated.load_metrics_from_sql(snap)

    assert hydrated.pagerank() == {n: snap[n]["pagerank"] for n in snap}
    assert hydrated.pagerank() == nx_pr  # equals the original NetworkX result
    assert hydrated.in_degree() == {n: snap[n]["in_degree"] for n in snap}

    # Dropping the structural graph must not break metric reads — they are
    # served from the loaded snapshot, proving the read path is SQL-routed.
    hydrated.release_graph()
    assert hydrated.graph().number_of_nodes() == 0
    assert hydrated.pagerank() == nx_pr
    assert hydrated.betweenness_centrality() == source.betweenness_centrality()
    assert hydrated.community_detection() == {n: snap[n]["community_id"] for n in snap}


def test_in_out_degree_reflect_import_edges():
    b = _build_sample()
    ind = b.in_degree()
    outd = b.out_degree()
    # c.py is imported by a and b → in-degree 2, no out-edges.
    assert ind["c.py"] == 2
    assert outd["c.py"] == 0
    # a.py imports two files, imported by none.
    assert outd["a.py"] == 2
    assert ind["a.py"] == 0


def test_mode_sql_backed_metrics_flag():
    assert OrchestratorMode.FAST.sql_backed_metrics is True
    assert OrchestratorMode.STANDARD.sql_backed_metrics is False
