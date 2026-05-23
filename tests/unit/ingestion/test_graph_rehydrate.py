"""Graph rehydration equivalence: ``GraphBuilder.from_persisted``.

Proves that a builder rehydrated from persisted node/edge/metric rows is
metric- and traversal-equivalent to the originally-built graph — without
running any resolution pass or recomputing centrality. This is the property
the incremental ``repowise update --full`` upgrade relies on.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.graph._rehydrate import _NODE_ATTR_KEYS
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile


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


def _serialize(builder: GraphBuilder) -> tuple[list[dict], list[dict]]:
    """Serialize the live graph to the dict shape the SQL readers return.

    Mirrors ``persistence.crud.get_all_graph_nodes`` / ``get_all_graph_edges``
    so the test exercises the real round-trip contract (note ``parent_name`` is
    persisted under the ``parent_symbol_id`` key).
    """
    graph = builder.graph()
    nodes: list[dict] = []
    for node_id, data in graph.nodes(data=True):
        row = {"node_id": node_id}
        for key in _NODE_ATTR_KEYS:
            if key == "parent_symbol_id":
                row[key] = data.get("parent_name")
            else:
                row[key] = data.get(key)
        nodes.append(row)
    edges: list[dict] = []
    for u, v, data in graph.edges(data=True):
        edges.append(
            {
                "source_node_id": u,
                "target_node_id": v,
                "edge_type": data.get("edge_type", "imports"),
                "confidence": data.get("confidence", 1.0),
                "imported_names": data.get("imported_names", []),
            }
        )
    return nodes, edges


def test_rehydrated_metrics_match_original():
    original = _build_sample()
    nodes, edges = _serialize(original)
    metrics = original.file_metrics_snapshot()

    hydrated = GraphBuilder.from_persisted(nodes, edges, metrics)

    # Finalized, with caches pre-filled — no implicit rebuild on read.
    assert hydrated._built is True
    assert hydrated._pagerank_cache is not None

    assert hydrated.pagerank() == original.pagerank()
    assert hydrated.betweenness_centrality() == original.betweenness_centrality()
    assert hydrated.community_detection() == original.community_detection()
    assert hydrated.in_degree() == original.in_degree()
    assert hydrated.out_degree() == original.out_degree()


def test_rehydrated_graph_is_traversal_equivalent():
    original = _build_sample()
    nodes, edges = _serialize(original)
    hydrated = GraphBuilder.from_persisted(nodes, edges, original.file_metrics_snapshot())

    og = original.graph()
    hg = hydrated.graph()
    assert og.number_of_nodes() == hg.number_of_nodes()
    assert og.number_of_edges() == hg.number_of_edges()

    for node in og.nodes:
        assert set(og.successors(node)) == set(hg.successors(node))
        assert set(og.predecessors(node)) == set(hg.predecessors(node))
        # Edge types survive the round-trip.
        for succ in og.successors(node):
            assert og[node][succ].get("edge_type") == hg[node][succ].get("edge_type")


def test_rehydrate_without_metrics_falls_back_to_recompute():
    """No snapshot supplied → caches stay empty and metrics recompute on read."""
    original = _build_sample()
    nodes, edges = _serialize(original)

    hydrated = GraphBuilder.from_persisted(nodes, edges, metrics=None)
    assert hydrated._pagerank_cache is None  # nothing pre-loaded
    # Recompute on the rehydrated structure still equals the original.
    assert hydrated.pagerank() == original.pagerank()
    assert hydrated.in_degree()["c.py"] == 2
