"""Performance-risk analysis helpers (the ``performance`` health dimension).

This package holds the I/O-boundary call-site classifier (:mod:`.io_boundaries`)
that the complexity walker's perf pass uses to turn a loop-nested call into a
typed ``io_in_loop`` finding, the sink-agnostic bounded reachability engine
(:mod:`.reachability`, Primitive 3), and the cross-function N+1 bridge
(:mod:`.crossfn`) built on top of both.
"""

from __future__ import annotations

from .crossfn import collect_crossfn_io_in_loop
from .io_boundaries import classify_call_sink, collect_io_names
from .reachability import ReachInfo, path_to_sink, reachable_to_sink

__all__ = [
    "ReachInfo",
    "classify_call_sink",
    "collect_crossfn_io_in_loop",
    "collect_io_names",
    "path_to_sink",
    "reachable_to_sink",
]
