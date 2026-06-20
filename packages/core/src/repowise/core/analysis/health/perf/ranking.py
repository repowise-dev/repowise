"""Primitive 2 — the severity ranker, reusable as a precision GATE.

The performance pillar already ranks findings by *blast radius*: a hit in a
widely-called, churny function matters more than the same hit in a cold leaf.
Phase 7b promotes that ranker from a sort key to a **precision gate**. Some
patterns are noisy when flagged everywhere (a bare O(n^2) nested loop; a
blocking I/O call outside any loop) but become high-signal the moment they sit
in a hot, request-reachable function. The gate fires those markers *only there*.

"Hot" combines two whole-program signals a file-local linter cannot compute:

  * **centrality** — the function's symbol node has a top-quintile number of
    distinct direct callers in the resolved ``calls`` graph (the ``CallGraphIndex``
    in-degree). A widely-called function is on many request paths, so a latency
    or quadratic cost there is paid often. Direct in-degree is a deterministic,
    ``O(E)`` proxy for request-reachability; transitive fan-in would be stronger
    but is quadratic to compute per function.
  * **churn** — the function's *file* is a git hotspot (``is_hotspot``) or sits
    in the repo's top-quintile of commit volume. Churny code is read and changed
    often, so a cost there is both more likely to bite and more worth fixing.

Either signal alone makes a function hot (logical OR): a hub utility with no
churn is still hot by centrality; a frequently-rewritten handler with few static
callers is still hot by churn. When neither the graph nor git metadata is
available the gate degrades to "nothing is hot" — the markers behind it simply
do not fire, never a false positive.
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

    Build once per ``analyze()`` from the shared :class:`CallGraphIndex` and the
    engine's ``git_meta_map``; call :meth:`is_hot` per candidate hit. Both the
    centrality threshold (top-quintile caller count) and the churn threshold
    (top-quintile commit volume) are computed up front from the repo-wide
    distributions, so the gate adapts to the repo instead of assuming a fixed
    bar.
    """

    __slots__ = ("_churn_p80", "_hot_in_degree", "_hotspot_files", "_index", "_meta")

    def __init__(
        self,
        index: CallGraphIndex | None,
        git_meta_map: dict[str, dict] | None,
        *,
        centrality_pct: float = 0.8,
        churn_pct: float = 0.8,
    ) -> None:
        self._index = index
        self._meta = git_meta_map or {}

        # Centrality bar: the top-quintile direct-caller count over functions
        # that have at least one caller. ``max(2, ...)`` keeps a tiny graph from
        # calling a function with a single caller "central".
        in_degrees = [d for d in (index.in_degree.values() if index else ()) if d >= 1]
        self._hot_in_degree = max(2, _percentile_threshold(in_degrees, centrality_pct))

        # Churn bar + the explicit hotspot set, both off git metadata.
        commit_counts: list[int] = []
        hotspot_files: set[str] = set()
        for path, meta in self._meta.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("is_hotspot"):
                hotspot_files.add(path)
            c = meta.get("commit_count_total")
            if isinstance(c, int) and c > 0:
                commit_counts.append(c)
        self._churn_p80 = _percentile_threshold(commit_counts, churn_pct)
        self._hotspot_files = hotspot_files

    # -- the gate -------------------------------------------------------------

    def is_central(self, path: str, func_start: int) -> bool:
        """True if the function at ``(path, func_start)`` is top-quintile-called."""
        if self._index is None:
            return False
        sid = self._index.resolve_function(path, func_start)
        if sid is None:
            return False
        return self._index.in_degree.get(sid, 0) >= self._hot_in_degree

    def is_churny(self, path: str) -> bool:
        """True if ``path`` is a git hotspot or in the top commit-volume quintile."""
        if path in self._hotspot_files:
            return True
        meta = self._meta.get(path)
        if not isinstance(meta, dict):
            return False
        c = meta.get("commit_count_total")
        return isinstance(c, int) and c >= self._churn_p80

    def is_hot(self, path: str, func_start: int) -> bool:
        """The OR gate: central OR churny ⇒ worth flagging a noisy-when-ungated
        marker here. Pure when neither graph nor git metadata is available."""
        return self.is_central(path, func_start) or self.is_churny(path)
