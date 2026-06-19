"""Cross-repo blast radius — reachability over the system graph.

Answers "if I change this service, what downstream services and repos are
affected?" by traversing the Phase 1 :class:`SystemGraph` *against* its edge
direction: an edge ``source → target`` means *source depends on / calls
target*, so changing ``target`` puts ``source`` at risk. We follow those
reverse links outward from the changed node(s) to find everything that could
break.

Two edge classes are weighted and labeled distinctly (the D5 honesty rule):

* **Structural** edges (http / grpc / event / package / db) assert a real
  dependency — a contract or an import. Impact propagates at full weight.
* **Behavioral** co-change edges only assert that two files *changed together*
  in history; they are correlation, not a call. They propagate impact at
  :data:`BEHAVIORAL_EDGE_WEIGHT` — the single knob that tunes "change together"
  against "call each other" in the ranking.

The cross-repo vocabulary mirrors the single-repo blast radius
(``packages/core/src/repowise/core/analysis/pr_blast.py`` + ``blast-radius.ts``):
impacted items are ranked by an impact ``score`` and carry a ``distance`` (hops
from the change). This module is pure and I/O-free — callers load the persisted
``system_graph.json`` (via :func:`load_system_graph` or the MCP enricher) and
hand the :class:`SystemGraph` in.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from repowise.core.workspace.system_graph import SystemEdge, SystemGraph

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

#: Default reachability depth. Cross-repo systems are shallow (a request rarely
#: crosses more than a handful of service boundaries), so a small default keeps
#: the impact set honest — distant, weakly-connected services are noise.
DEFAULT_MAX_DEPTH = 3

#: Hard ceiling on traversal depth so a pathological graph can't run away.
MAX_DEPTH_LIMIT = 8

#: Behavioral (co-change) edges assert correlation, not a real call, so they
#: propagate impact at a fraction of a structural edge's weight. This is the one
#: knob that separates "these change together" from "these call each other" in
#: the reachability ranking — keep it the only place that distinction is tuned.
BEHAVIORAL_EDGE_WEIGHT = 0.5

#: Each additional hop attenuates the propagated impact: a service two calls away
#: is less likely to actually break than a direct consumer. Applied once per hop,
#: so a node at distance *d* carries roughly ``DISTANCE_DECAY ** d`` of the base.
DISTANCE_DECAY = 0.6

#: Cap on the number of impacted nodes returned. ``total_impacted`` always
#: reflects the true count; this only bounds the returned list (and the artifact)
#: so a high-fan-out hub can't bloat a response. Mirrors the trimming convention
#: in the single-repo risk tool.
MAX_IMPACTED = 100


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ImpactedNode:
    """A service reachable from the change, with its ranked impact."""

    id: str  # system-graph node id
    repo: str
    name: str
    kind: str  # service | frontend | worker | library | external
    distance: int  # hops from the nearest changed node (1 = direct dependent)
    score: float  # 0-1 ranked impact (distance decay + behavioral weight baked in)
    structural: bool  # reachable via an all-structural path (a real dependency)
    edge_kinds: list[str] = field(default_factory=list)  # kinds carrying impact in

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo": self.repo,
            "name": self.name,
            "kind": self.kind,
            "distance": self.distance,
            "score": self.score,
            "structural": self.structural,
            "edge_kinds": self.edge_kinds,
        }


@dataclass
class CrossRepoBlastRadius:
    """The reachable impact set for one or more changed services."""

    targets: list[str]  # resolved node ids the traversal started from
    target_repos: list[str]  # distinct repos of the targets
    impacted: list[ImpactedNode]  # ranked, capped at MAX_IMPACTED
    impacted_repos: list[str]  # distinct repos in the impact set (excl. targets)
    structural_count: int  # impacted nodes reachable via a real dependency
    behavioral_count: int  # impacted nodes reachable only via co-change
    max_distance: int  # deepest hop reached
    total_impacted: int  # true count before MAX_IMPACTED capping
    unresolved_targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "target_repos": self.target_repos,
            "impacted": [n.to_dict() for n in self.impacted],
            "impacted_repos": self.impacted_repos,
            "structural_count": self.structural_count,
            "behavioral_count": self.behavioral_count,
            "max_distance": self.max_distance,
            "total_impacted": self.total_impacted,
            "unresolved_targets": self.unresolved_targets,
        }


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def resolve_targets(graph: SystemGraph, raw_targets: list[str]) -> tuple[list[str], list[str]]:
    """Map caller-supplied target strings to system-graph node ids.

    Accepts an exact node id (``"repo"`` or ``"repo::service/path"``) or a repo
    alias (expands to every node in that repo). Returns ``(resolved, unresolved)``
    where ``resolved`` is a sorted, de-duplicated list of node ids.
    """
    node_ids = {n.id for n in graph.nodes}
    nodes_by_repo: dict[str, list[str]] = defaultdict(list)
    for n in graph.nodes:
        nodes_by_repo[n.repo].append(n.id)

    resolved: set[str] = set()
    unresolved: list[str] = []
    for raw in raw_targets:
        if raw in node_ids:
            resolved.add(raw)
        elif raw in nodes_by_repo:
            resolved.update(nodes_by_repo[raw])
        else:
            unresolved.append(raw)
    return sorted(resolved), unresolved


# ---------------------------------------------------------------------------
# Reachability (pure)
# ---------------------------------------------------------------------------


@dataclass
class _Accum:
    """Per-node accumulator during the outward traversal."""

    distance: int
    score: float
    structural: bool  # any all-structural path reaches it
    edge_kinds: set[str]


def _build_impact_adjacency(
    edges: list[SystemEdge],
) -> dict[str, list[tuple[str, SystemEdge]]]:
    """Adjacency in *impact* direction: changed-node id → (dependent id, edge).

    Structural edge ``source → target`` (source depends on target): changing
    ``target`` impacts ``source``, so we add ``target → source``. Behavioral
    co-change edges are symmetric, so both endpoints impact each other.
    """
    adj: dict[str, list[tuple[str, SystemEdge]]] = defaultdict(list)
    for e in edges:
        if e.structural:
            adj[e.target].append((e.source, e))
        else:
            adj[e.source].append((e.target, e))
            adj[e.target].append((e.source, e))
    return adj


def cross_repo_blast_radius(
    graph: SystemGraph,
    targets: list[str],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    include_behavioral: bool = True,
) -> CrossRepoBlastRadius:
    """Compute the downstream impact set for *targets* over the system graph.

    Traverses reverse dependency edges outward from the resolved target nodes,
    ranking each reachable service by an impact ``score`` that bakes in distance
    decay and the structural/behavioral weighting. Cycle-safe (bounded by
    ``max_depth`` and convergent score relaxation) and pure.
    """
    depth = max(1, min(max_depth, MAX_DEPTH_LIMIT))
    resolved, unresolved = resolve_targets(graph, targets)

    nodes_by_id = {n.id: n for n in graph.nodes}
    target_set = set(resolved)
    target_repos = sorted({nodes_by_id[t].repo for t in resolved if t in nodes_by_id})

    if not resolved:
        return CrossRepoBlastRadius(
            targets=resolved,
            target_repos=target_repos,
            impacted=[],
            impacted_repos=[],
            structural_count=0,
            behavioral_count=0,
            max_distance=0,
            total_impacted=0,
            unresolved_targets=unresolved,
        )

    edges = graph.edges
    if not include_behavioral:
        edges = [e for e in edges if e.structural]
    adj = _build_impact_adjacency(edges)

    best: dict[str, _Accum] = {}
    # Frontier value per node: (propagated score, path-is-all-structural).
    frontier: dict[str, tuple[float, bool]] = {t: (1.0, True) for t in resolved}

    for hop in range(1, depth + 1):
        next_frontier: dict[str, tuple[float, bool]] = {}
        for src, (src_score, src_structural) in frontier.items():
            for dst, edge in adj.get(src, []):
                if dst in target_set:
                    continue  # never impact a changed node back onto itself
                factor = 1.0 if edge.structural else BEHAVIORAL_EDGE_WEIGHT
                cand_score = src_score * edge.confidence * factor * DISTANCE_DECAY
                if cand_score <= 0.0:
                    continue
                path_structural = src_structural and edge.structural

                acc = best.get(dst)
                if acc is None:
                    acc = _Accum(
                        distance=hop,
                        score=cand_score,
                        structural=path_structural,
                        edge_kinds={edge.kind},
                    )
                    best[dst] = acc
                else:
                    acc.distance = min(acc.distance, hop)
                    acc.score = max(acc.score, cand_score)
                    acc.structural = acc.structural or path_structural
                    acc.edge_kinds.add(edge.kind)

                # Only keep relaxing while the propagated score improves —
                # convergent, so cycles terminate well before max_depth.
                prev = next_frontier.get(dst)
                if prev is None or cand_score > prev[0]:
                    next_frontier[dst] = (cand_score, path_structural)
        if not next_frontier:
            break
        frontier = next_frontier

    impacted = [
        ImpactedNode(
            id=nid,
            repo=nodes_by_id[nid].repo if nid in nodes_by_id else "",
            name=nodes_by_id[nid].name if nid in nodes_by_id else nid,
            kind=nodes_by_id[nid].kind if nid in nodes_by_id else "service",
            distance=acc.distance,
            score=round(acc.score, 4),
            structural=acc.structural,
            edge_kinds=sorted(acc.edge_kinds),
        )
        for nid, acc in best.items()
    ]
    # Rank: strongest impact first, then nearest, then stable by id.
    impacted.sort(key=lambda n: (-n.score, n.distance, n.id))

    total = len(impacted)
    structural_count = sum(1 for n in impacted if n.structural)
    behavioral_count = total - structural_count
    max_distance = max((n.distance for n in impacted), default=0)
    impacted_repos = sorted({n.repo for n in impacted if n.repo not in target_repos})

    return CrossRepoBlastRadius(
        targets=resolved,
        target_repos=target_repos,
        impacted=impacted[:MAX_IMPACTED],
        impacted_repos=impacted_repos,
        structural_count=structural_count,
        behavioral_count=behavioral_count,
        max_distance=max_distance,
        total_impacted=total,
        unresolved_targets=unresolved,
    )
