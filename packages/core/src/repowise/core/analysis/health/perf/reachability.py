"""Bounded-depth reachability to a typed sink (sink-agnostic).

This is *Primitive 3* of the performance-pillar arc: the engine that answers
"starting from this node and following call edges, does a path of at most
``max_depth`` hops reach a node that satisfies a sink predicate?" — and, when
it does, returns the path for explainability.

It is built for cross-function N+1 detection (a loop in function ``A`` calls a
helper that, transitively, executes an I/O boundary), but **nothing here is
performance-specific**. The two things that vary between use cases are both
parameters:

  * **the sink set** — the caller passes the node ids that *are* sinks. For
    performance that is "functions holding a bare I/O sink"; the identical walk
    serves static security taint (``sink = dangerous API``) and architecture
    conformance (``sink = forbidden layer``) by passing a different set.
  * **the adjacency** — the caller passes a ``predecessors`` callable (the
    *reverse* of the edge direction being followed). The walk never imports a
    graph library; it only calls ``predecessors(node)``.

**Why a reverse BFS from the sinks.** The question "can N reach a sink within K
forward hops?" is answered for *every* N at once by a single multi-source BFS
that starts at the sinks and walks edges backwards. Each node and edge within
the depth bound is visited once, so the whole repo's reachability is computed
in one ``O(V + E)`` pass (bounded by ``max_depth``) and is memoized by
construction — there is no per-query re-walk and no risk of blow-up on a dense
call graph. This is what keeps the cross-function pass inside the index-time
budget.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReachInfo:
    """How a node reaches its nearest sink along the bounded walk.

    ``distance`` is the number of forward hops to ``sink`` (0 when the node is
    itself a sink). ``next_hop`` is the successor one step closer to the sink
    (``None`` for a sink). Follow ``next_hop`` repeatedly to recover the path.
    """

    distance: int
    next_hop: str | None
    sink: str


def reachable_to_sink(
    sink_nodes: Iterable[str],
    predecessors: Callable[[str], Iterable[str]],
    *,
    max_depth: int = 3,
) -> dict[str, ReachInfo]:
    """Map every node within ``max_depth`` forward hops of a sink to a :class:`ReachInfo`.

    ``sink_nodes`` are the nodes that satisfy the (caller-defined) sink
    predicate. ``predecessors(node)`` yields the nodes with a forward edge
    *into* ``node`` (i.e. reverse adjacency). The result contains an entry for
    each sink (distance 0) and for each node that can reach some sink in
    ``1..max_depth`` forward hops; nodes that cannot reach a sink are absent.

    Deterministic: BFS visits nodes in non-decreasing distance, so the first
    (and recorded) distance for any node is the minimum, and the recorded
    ``sink`` is the nearest one (ties broken by BFS/seed order).
    """
    if max_depth < 0:
        return {}

    info: dict[str, ReachInfo] = {}
    queue: deque[str] = deque()
    for s in sink_nodes:
        if s not in info:
            info[s] = ReachInfo(distance=0, next_hop=None, sink=s)
            queue.append(s)

    while queue:
        node = queue.popleft()
        cur = info[node]
        if cur.distance >= max_depth:
            continue
        for pred in predecessors(node):
            if pred in info:
                continue  # already reached at an equal-or-shorter distance
            info[pred] = ReachInfo(
                distance=cur.distance + 1,
                next_hop=node,
                sink=cur.sink,
            )
            queue.append(pred)
    return info


def path_to_sink(node: str, info: dict[str, ReachInfo], *, max_len: int = 16) -> list[str]:
    """Reconstruct ``[node, ..., sink]`` by following ``next_hop`` pointers.

    Returns ``[]`` when ``node`` has no :class:`ReachInfo` (it does not reach a
    sink within the bound). ``max_len`` is a defensive cap so a corrupted
    pointer chain can never loop forever; it is never hit for a BFS-built map
    bounded at a small ``max_depth``.
    """
    if node not in info:
        return []
    path: list[str] = [node]
    cur = info[node]
    seen = {node}
    while cur.next_hop is not None and len(path) < max_len:
        nxt = cur.next_hop
        if nxt in seen:  # impossible for a BFS map; defensive only
            break
        path.append(nxt)
        seen.add(nxt)
        cur = info.get(nxt)
        if cur is None:
            break
    return path
