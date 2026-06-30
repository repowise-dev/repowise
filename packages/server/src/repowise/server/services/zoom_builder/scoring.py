"""Execution-aware importance scoring for the zoom map ("how it runs").

A purely structural tree (biggest folder first) reads like a dependency graph.
The differentiator here is that a file's importance folds in *execution* signal:
whether it is an entry point, how close it sits to one along the import graph,
and whether the guided tour visits it. That is blended with centrality and a
light size term, with test/dead files down-weighted. The renderer's density
caps then surface execution-relevant nodes first at every zoom depth.

Two stages:
  1. ``compute_file_signals`` — per-file signals, incl. BFS distance from the
     entry points over import edges (``on_flow`` = reachable).
  2. ``score_tree`` — per-file raw importance, rolled up to parents, then
     normalized within each sibling set into ``importance`` (0..1) +
     ``sibling_rank`` (1 = most important sibling).

Pure: no DB, no session.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from .models import ZoomNode

# Edge types that represent "uses at runtime" for reachability. Symbol-level
# containment edges are already excluded upstream (file-only architecture view).
_FLOW_EDGE_TYPES = frozenset({"imports", "depends_on"})

_SIZE_WEIGHT = {"simple": 0.0, "moderate": 0.5, "complex": 1.0}


@dataclass(frozen=True)
class FileStat:
    """Raw per-file metrics pulled from the architecture view."""

    path: str
    pagerank_pct: float = 0.0  # 0..100
    betweenness: float = 0.0
    degree: int = 0            # in + out
    complexity: str = "simple"
    is_entry_point: bool = False
    is_test: bool = False
    is_hotspot: bool = False
    is_dead: bool = False


@dataclass(frozen=True)
class FileSignal:
    """Derived per-file signals consumed by the tree (leaf flags) + scorer."""

    pagerank_pct: float
    betweenness: float
    degree: int
    complexity: str
    is_entry_point: bool
    is_test: bool
    is_hotspot: bool
    is_dead: bool
    on_tour: bool
    entry_dist: int | None  # BFS hops from nearest entry point; None = unreachable
    on_flow: bool           # entry_dist is not None


def _bfs_distances(
    seeds: list[str],
    adjacency: dict[str, list[str]],
    known: set[str],
) -> dict[str, int]:
    """Shortest hop count from any seed to each reachable file."""
    dist: dict[str, int] = {}
    queue: deque[str] = deque()
    for s in seeds:
        if s in known and s not in dist:
            dist[s] = 0
            queue.append(s)
    while queue:
        cur = queue.popleft()
        for nxt in adjacency.get(cur, ()):
            if nxt in known and nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)
    return dist


def compute_file_signals(
    file_stats: list[FileStat],
    edges: list[tuple[str, str, str]],
    entry_points: list[str],
    tour_paths: set[str],
) -> dict[str, FileSignal]:
    """Build per-file signals, including reachability from the entry points."""
    known = {fs.path for fs in file_stats}
    adjacency: dict[str, list[str]] = {}
    for src, tgt, etype in edges:
        if etype in _FLOW_EDGE_TYPES and src in known and tgt in known:
            adjacency.setdefault(src, []).append(tgt)

    dist = _bfs_distances(entry_points, adjacency, known)

    signals: dict[str, FileSignal] = {}
    for fs in file_stats:
        d = dist.get(fs.path)
        signals[fs.path] = FileSignal(
            pagerank_pct=fs.pagerank_pct,
            betweenness=fs.betweenness,
            degree=fs.degree,
            complexity=fs.complexity,
            is_entry_point=fs.is_entry_point,
            is_test=fs.is_test,
            is_hotspot=fs.is_hotspot,
            is_dead=fs.is_dead,
            on_tour=fs.path in tour_paths,
            entry_dist=d,
            on_flow=d is not None,
        )
    return signals


def _minmax(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    lo, hi = min(values), max(values)
    return lo, (hi if hi > lo else lo + 1.0)


def _file_raw(sig: FileSignal, deg_lo: float, deg_span: float, bet_lo: float, bet_span: float) -> float:
    pr = sig.pagerank_pct / 100.0
    deg_n = (sig.degree - deg_lo) / deg_span
    bet_n = (sig.betweenness - bet_lo) / bet_span
    centrality = 0.5 * pr + 0.3 * deg_n + 0.2 * bet_n

    entry = 1.0 if sig.is_entry_point else 0.0
    proximity = 1.0 / (1.0 + sig.entry_dist) if sig.entry_dist is not None else 0.0
    on_tour = 1.0 if sig.on_tour else 0.0
    execution = 0.5 * entry + 0.35 * proximity + 0.15 * on_tour

    hotspot = 1.0 if sig.is_hotspot else 0.0
    size = _SIZE_WEIGHT.get(sig.complexity, 0.0)

    raw = 0.30 * centrality + 0.45 * execution + 0.15 * hotspot + 0.10 * size
    if sig.is_test:
        raw *= 0.4
    if sig.is_dead:
        raw *= 0.3
    return max(raw, 0.0)


def score_tree(
    root_id: str,
    nodes: dict[str, ZoomNode],
    signals: dict[str, FileSignal],
) -> dict[str, ZoomNode]:
    """Return ``nodes`` with ``importance`` (0..1) + ``sibling_rank`` filled.

    File raw scores come from ``signals``; a parent's raw score blends the max
    and mean of its children (so one critical file lifts its folder without a
    large inert folder dominating by sheer count). Importance is then normalized
    within each sibling set, so it is directly comparable to the density caps
    and treemap areas the renderer uses per parent.
    """
    deg_vals = [float(s.degree) for s in signals.values()]
    bet_vals = [s.betweenness for s in signals.values()]
    deg_lo, deg_hi = _minmax(deg_vals)
    bet_lo, bet_hi = _minmax(bet_vals)
    deg_span = deg_hi - deg_lo
    bet_span = bet_hi - bet_lo

    raw: dict[str, float] = {}

    def compute_raw(node_id: str) -> float:
        node = nodes[node_id]
        if node.kind == "file":
            sig = signals.get(node.path)
            value = _file_raw(sig, deg_lo, deg_span, bet_lo, bet_span) if sig else 0.0
            raw[node_id] = value
            return value
        child_raws = [compute_raw(c) for c in node.children]
        if child_raws:
            value = 0.6 * max(child_raws) + 0.4 * (sum(child_raws) / len(child_raws))
        else:
            value = 0.0
        raw[node_id] = value
        return value

    compute_raw(root_id)

    updated: dict[str, ZoomNode] = dict(nodes)

    def normalize_children(node_id: str) -> None:
        node = updated[node_id]
        children = list(node.children)
        if children:
            ranked = sorted(children, key=lambda c: (-raw[c], updated[c].name))
            top = raw[ranked[0]] or 1.0
            for rank, cid in enumerate(ranked, start=1):
                updated[cid] = replace(
                    updated[cid],
                    importance=raw[cid] / top,
                    sibling_rank=rank,
                )
            for cid in children:
                normalize_children(cid)

    # The root has no siblings; pin it to full importance.
    updated[root_id] = replace(updated[root_id], importance=1.0, sibling_rank=1)
    normalize_children(root_id)
    return updated
