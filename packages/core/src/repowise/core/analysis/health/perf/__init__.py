"""Performance-risk analysis helpers (the ``performance`` health dimension).

This package holds:

* the shared import → I/O-boundary classifier (:mod:`.io_boundaries`) that maps
  a file's imported names to ``io_kind``;
* the per-language perf :mod:`.dialects` (callee extraction + execution-sink
  lexicon + loop / string / async predicates + the per-language marker list)
  that the complexity walker drives the perf pass off;
* the sink-agnostic bounded reachability engine (:mod:`.reachability`) and the
  cross-function N+1 bridge (:mod:`.crossfn`) built on it;
* the shared call-graph index (:mod:`.callgraph`) + the severity ranker
  (:mod:`.ranking`) used as a centrality precision gate, and the
  centrality-gated markers (:mod:`.gated`);
* the causal read model that folds raw findings into ranked opportunities:
  :mod:`.opportunities` is its public face, over :mod:`.facts` (typed evidence
  read off a row), :mod:`.causal` (grouping and the versioned identity kernel),
  :mod:`.actionability` (whether the evidence supports naming a change), and
  :mod:`.opportunity_rank` (how much it costs and in what order it lands).

Nothing here touches persistence or transport. Stored rows arrive through the
same attribute adapter analyzer dataclasses do.
"""

from __future__ import annotations

from .callgraph import CallGraphIndex
from .crossfn import collect_crossfn_io_in_loop
from .dialects import PERF_DIALECTS, BasePerfDialect
from .gated import collect_blocking_io_under_lock, collect_centrality_gated
from .io_boundaries import collect_io_names
from .opportunities import (
    PERFORMANCE_MODEL_VERSION,
    PerformanceFix,
    PerformanceOpportunity,
    build_performance_opportunities,
    link_performance_findings,
    model_state,
)
from .promotion import apply_perf_promotions
from .ranking import PerfRanker
from .reachability import ReachInfo, path_to_sink, reachable_to_sink

__all__ = [
    "PERFORMANCE_MODEL_VERSION",
    "PERF_DIALECTS",
    "BasePerfDialect",
    "CallGraphIndex",
    "PerfRanker",
    "PerformanceFix",
    "PerformanceOpportunity",
    "ReachInfo",
    "apply_perf_promotions",
    "build_performance_opportunities",
    "collect_blocking_io_under_lock",
    "collect_centrality_gated",
    "collect_crossfn_io_in_loop",
    "collect_io_names",
    "link_performance_findings",
    "model_state",
    "path_to_sink",
    "reachable_to_sink",
]
