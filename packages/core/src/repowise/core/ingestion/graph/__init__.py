"""Dependency graph builder for the repowise ingestion pipeline.

GraphBuilder constructs a directed graph from ParsedFile objects with two
tiers of nodes:

    File-level nodes:
        "file"     — every source file
        "external" — third-party / unresolvable imports (prefix "external:")

    Symbol-level nodes:
        "symbol"   — functions, classes, methods, interfaces, etc.
                     keyed by Symbol.id (e.g. "src/app.py::main")

Edge types:
    "imports"     — file-to-file import relationship
    "defines"     — file-to-symbol containment
    "has_method"  — class-to-method ownership
    "calls"       — symbol-to-symbol call relationship (with confidence)

After calling build(), graph metrics are available:
    pagerank()                  — dict[path, float]
    strongly_connected_components() — list[frozenset[str]]
    betweenness_centrality()    — dict[path, float]
    in_degree() / out_degree()  — dict[path, int]

Internal layout (kept under the 400-line ceiling):
    builder.py      — GraphBuilder core (nodes, edges, build lifecycle)
    _metrics.py     — centrality / community / degree metrics (+ SQL routing)
    _resolvers.py   — heritage / member-read / call resolution passes
    _edges.py       — co-change / dynamic / framework edges
    _serialize.py   — node-link JSON + SQLite export
    _rehydrate.py   — rebuild a builder from persisted nodes/edges/metrics
    _stem.py        — import-stem resolution helpers
"""

from __future__ import annotations

from ._stem import _stem_priority, build_stem_map
from .builder import GraphBuilder

__all__ = ["GraphBuilder", "_stem_priority", "build_stem_map"]
