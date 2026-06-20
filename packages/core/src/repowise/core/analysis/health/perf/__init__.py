"""Performance-risk analysis helpers (the ``performance`` health dimension).

This package holds:

* the shared import → I/O-boundary classifier (:mod:`.io_boundaries`) that maps
  a file's imported names to ``io_kind``;
* the per-language perf :mod:`.dialects` (callee extraction + execution-sink
  lexicon + loop / string / async predicates + the per-language marker list)
  that the complexity walker drives the perf pass off;
* the sink-agnostic bounded reachability engine (:mod:`.reachability`,
  Primitive 3) and the cross-function N+1 bridge (:mod:`.crossfn`) built on it.
"""

from __future__ import annotations

from .crossfn import collect_crossfn_io_in_loop
from .dialects import PERF_DIALECTS, BasePerfDialect
from .io_boundaries import collect_io_names
from .reachability import ReachInfo, path_to_sink, reachable_to_sink

__all__ = [
    "PERF_DIALECTS",
    "BasePerfDialect",
    "ReachInfo",
    "collect_crossfn_io_in_loop",
    "collect_io_names",
    "path_to_sink",
    "reachable_to_sink",
]
