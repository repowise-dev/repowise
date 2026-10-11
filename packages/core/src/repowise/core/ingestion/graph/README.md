# `graph`

Builds the directed dependency graph for a repository from `ParsedFile`
objects and exposes the graph metrics (PageRank, betweenness, communities,
degrees) that downstream selection, generation, and risk analysis consume.

## Purpose

`GraphBuilder` is the analysis stage between parsing and generation. It adds
file/symbol nodes, resolves imports, heritage, member reads, and calls into a
NetworkX `DiGraph`, then computes centrality and community metrics over
file- and symbol-level subgraphs.

## Public API

```python
from repowise.core.ingestion.graph import GraphBuilder

b = GraphBuilder(repo_path)
for parsed in parsed_files:
    b.add_file(parsed)
b.build()
pr  = b.pagerank()                 # dict[path, float]
bc  = b.betweenness_centrality()
cd  = b.community_detection()
ind = b.in_degree()                # dict[path, int]
```

Edge augmentation: `add_co_change_edges`, `add_dynamic_edges`,
`add_framework_edges`. Serialisation: `to_json`, `persist`.

### Large-repo SQL metric routing

For large repos the file-level metrics are materialized to the `graph_metrics`
table (see `persistence.crud.batch_upsert_graph_metrics`). After that:

- `b.file_metrics_snapshot()` returns the `node_id → {pagerank, betweenness,
  community_id, in_degree, out_degree}` dict written to SQL.
- `b.load_metrics_from_sql(rows)` pre-fills the metric caches from the
  materialized snapshot, so the expensive NetworkX kernels are never recomputed
  — reads are served from SQL.
- `b.release_graph()` drops the in-memory NetworkX object once metrics are
  loaded, for callers that no longer need traversal (e.g. the fast-mode
  pipeline, which generates no docs).

This is gated by the pipeline knob `OrchestratorMode` / `sql_backed_metrics`
(default off in standard mode; on in fast mode). Standard generation keeps the
graph live because it traverses it for context assembly.

### Rehydrating a builder from SQL

`GraphBuilder.from_persisted(nodes, edges, metrics)` rebuilds a finalized
builder directly from persisted rows (`persistence.get_all_graph_nodes` /
`get_all_graph_edges` / `get_graph_metrics`) — **without re-parsing or
re-resolving** imports/calls/heritage. It reconstructs the NetworkX graph and
calls `load_metrics_from_sql`, so the result is metric- and
traversal-equivalent to the originally-built graph. This is what powers the
incremental `repowise update --full` fast→full upgrade: doc generation runs
against the rehydrated graph, skipping the expensive resolution + centrality
recompute that the fast index already did.

## Internal layout

- `builder.py` — `GraphBuilder` core: node/edge construction + `build()`.
- `_metrics.py` — `MetricsMixin`: centrality, communities, degrees, SQL routing.
- `_resolvers.py` — `ResolveMixin`: heritage / member-read / call passes.
- `_edges.py` — `EdgesMixin`: co-change / dynamic / framework edges.
- `_serialize.py` — `SerializeMixin`: node-link JSON + SQLite export.
- `_rehydrate.py` — `RehydrateMixin`: `from_persisted` rebuild from SQL rows.
- `_stem.py` — import-stem priority + stem-map construction.

## Extension points

- New edge kind: add a method to `EdgesMixin` or a resolution pass to
  `ResolveMixin`, then call it from `build()`.
- New metric: add a cached method to `MetricsMixin` and include it in
  `file_metrics_snapshot()` if it should be materialized.

## Tests

- `tests/unit/ingestion/test_graph.py`
- `tests/unit/ingestion/test_graph_metrics_sql.py`
- `tests/integration/persistence/test_graph_metrics_pg.py`
"""
