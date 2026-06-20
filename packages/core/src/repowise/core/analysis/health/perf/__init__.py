"""Performance-risk analysis helpers (the ``performance`` health dimension).

Today this package holds the I/O-boundary call-site classifier
(:mod:`.io_boundaries`) that the complexity walker's perf pass uses to turn a
loop-nested call into a typed ``io_in_loop`` finding. PR4 will add the
cross-function reachability engine alongside it.
"""

from __future__ import annotations

from .io_boundaries import classify_call_sink, collect_io_names

__all__ = ["classify_call_sink", "collect_io_names"]
