"""The severity ranker, reusable as a precision GATE.

The performance pillar already ranks findings by *blast radius*: a hit in a
widely-called function matters more than the same hit in a cold leaf. The
ranker is also used as a **precision gate**, not only a sort key. Some patterns
are noisy when flagged everywhere (a bare O(n^2) nested loop; a blocking I/O
call outside any loop) but become high-signal the moment they sit in a hot,
request-reachable function. The gate fires those markers *only there*.

"Hot" is one whole-program signal a file-local linter cannot compute:

  * **centrality** - the function's symbol node has a top-quintile number of
    distinct direct predecessors in the reliable execution graph (the
    ``CallGraphIndex`` in-degree). A widely-reached function is on many request
    paths, so a latency or quadratic cost there is paid often. Direct in-degree
    is a deterministic, ``O(E)`` proxy for request-reachability; transitive
    fan-in would be stronger but is quadratic to compute per function.

Churn is deliberately not a second arm. How often a file is edited is not how
often it runs, so it cannot support the request-reachability these markers
assert in their own reason text. Churn still earns rank in
``opportunity_rank``; it does not decide whether a finding exists.

Without a graph the gate degrades to "nothing is hot" - the markers behind it
simply do not fire, never a false positive.
"""

from __future__ import annotations

from .callgraph import CallGraphIndex


def _percentile_threshold(values: list[int], pct: float) -> int:
    """The ``pct`` percentile of *values* (inclusive-lower), or a high sentinel.

    Mirrors ``engine._percentile_p80``'s convention so the centrality / churn
    quintiles agree with the rest of the health pipeline. Returns a value larger
    than any input when *values* is empty, so an empty distribution gates
    everything out (nothing clears an unreachable bar).
    """
    if not values:
        return 1 << 30
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(pct * len(ordered))))
    return ordered[idx]


class PerfRanker:
    """Decides whether a function is *hot* enough for a centrality-gated marker.

    Build once per ``analyze()`` from the shared :class:`CallGraphIndex`; call
    :meth:`is_hot` per candidate hit. The centrality threshold (top-quintile
    caller count) is computed up front from the repo-wide distribution, so the
    gate adapts to the repo instead of assuming a fixed bar.
    """

    __slots__ = ("_hot_in_degree", "_index")

    def __init__(
        self,
        index: CallGraphIndex | None,
        *,
        centrality_pct: float = 0.8,
    ) -> None:
        self._index = index

        # Centrality bar: the top-quintile direct-caller count over functions
        # that have at least one caller. ``max(2, ...)`` keeps a shallow graph
        # from calling a function with a single caller "central".
        in_degrees = [d for d in (index.in_degree.values() if index else ()) if d >= 1]
        self._hot_in_degree = max(2, _percentile_threshold(in_degrees, centrality_pct))

    # -- the gate -------------------------------------------------------------

    def is_central(self, path: str, func_start: int) -> bool:
        """True if the function at ``(path, func_start)`` is top-quintile-called."""
        if self._index is None:
            return False
        sid = self._index.resolve_function(path, func_start)
        if sid is None:
            return False
        return self._index.in_degree.get(sid, 0) >= self._hot_in_degree

    def is_hot(self, path: str, func_start: int) -> bool:
        """Central enough to carry a marker that claims request-reachability.

        Pure when no graph is available: nothing is hot, so nothing fires.
        """
        return self.is_central(path, func_start)
