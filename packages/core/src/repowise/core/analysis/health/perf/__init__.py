"""Performance-risk analysis helpers (the ``performance`` health dimension).

This package holds:

* the shared import → I/O-boundary classifier (:mod:`.io_boundaries`) that maps
  a file's imported names to ``io_kind``;
* the per-language perf :mod:`.dialects` (callee extraction + execution-sink
  lexicon + loop / string / async predicates + the per-language marker list)
  that the complexity walker drives the perf pass off;
* the sink-agnostic bounded reachability engine (:mod:`.reachability`,
  Primitive 3) and the cross-function N+1 bridge (:mod:`.crossfn`) built on it;
* the shared call-graph index (:mod:`.callgraph`) + the severity ranker
  (:mod:`.ranking`, Primitive 2) used as a centrality precision gate, and the
  centrality-gated Phase-7b markers (:mod:`.gated`).
"""

from __future__ import annotations

from .callgraph import CallGraphIndex
from .crossfn import collect_crossfn_io_in_loop
from .dialects import PERF_DIALECTS, BasePerfDialect
from .gated import collect_blocking_io_under_lock, collect_centrality_gated
from .io_boundaries import collect_io_names
from .ranking import PerfRanker
from .reachability import ReachInfo, path_to_sink, reachable_to_sink

__all__ = [
    "PERF_DIALECTS",
    "BasePerfDialect",
    "CallGraphIndex",
    "PerfRanker",
    "ReachInfo",
    "collect_blocking_io_under_lock",
    "collect_centrality_gated",
    "collect_crossfn_io_in_loop",
    "collect_io_names",
    "path_to_sink",
    "reachable_to_sink",
]
