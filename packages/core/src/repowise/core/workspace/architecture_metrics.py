"""Architecture-complexity metrics over the workspace system graph.

A workspace can already see every service, every typed edge, every cycle, and
every conformance violation. What it cannot get from those descriptive views is
a single *evaluative* read: how coupled is the whole system, and which services
form its architectural core. This module computes the standard
MacCormack / Baldwin / Sturtevant complexity metrics over the
:class:`~repowise.core.workspace.system_graph.SystemGraph` every other workspace
view reads, and rolls them into one deterministic 1-10 architecture score.

**Three outputs, one pass:**

* *Per workspace* — propagation cost, core size and ratio, cycle count, an
  architecture-type label (core-periphery vs hierarchical), and the score.
* *Per service* — visibility fan-in / fan-out and a core-periphery role
  (Core / Shared / Control / Peripheral).
* *Benchmarkable numbers* — propagation cost and the score are comparable across
  workspaces and over time.

**Structural edges only.** Visibility is the transitive closure over the
``edge.structural`` edges (http / grpc / event / package / db) — co-change is
behavioral and never asserts a dependency, so it is excluded from every metric
here. The structural/behavioral distinction is the one the system graph already
encodes (see :data:`system_graph._STRUCTURAL_KINDS`).

**Reuse, not reinvention.** The largest cyclic group is the largest
strongly-connected component, computed with NetworkX (the same library
:mod:`cycles` and the single-repo graph metrics use). The cycle *count* fed into
the score is :func:`cycles.detect_cycles`, so the number matches what the DSM and
the conformance report already show. No second cycle/SCC implementation, no LLM.

Pure and I/O-free: it consumes a ``SystemGraph`` (plus an optional
already-computed conformance violation count) and returns dataclasses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from repowise.core.workspace.cycles import detect_cycles
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph

_log = logging.getLogger("repowise.workspace.architecture_metrics")

# ---------------------------------------------------------------------------
# Tuning constants (single source of truth)
#
# Every weight, cap, and cutoff the metric depends on lives here — no magic
# numbers scattered across the functions below. Lower coupling and a smaller
# cyclic core always map to a *higher* score.
# ---------------------------------------------------------------------------

#: Score bounds. Matches the Code Health 1-10 convention (a 0 is never shown).
SCORE_MAX = 10.0
SCORE_MIN = 1.0

#: Points subtracted from :data:`SCORE_MAX` per driver. The propagation-cost and
#: core-ratio terms scale with their 0-1 fractions; the cycle and violation terms
#: accrue per finding up to a cap so a pathological tangle can't drive the score
#: arbitrarily negative before clamping.
PROPAGATION_COST_WEIGHT = 5.0  # full reachability (pc = 1.0) costs 5 points
CORE_RATIO_WEIGHT = 3.0  # the whole system in one cyclic core costs 3 points
CYCLE_PENALTY_PER = 0.5  # per elementary dependency cycle
CYCLE_PENALTY_CAP = 2.0
VIOLATION_PENALTY_PER = 0.5  # per declared-rule conformance violation
VIOLATION_PENALTY_CAP = 2.0

#: A system whose largest cyclic group spans at least this fraction of its
#: services is "core-periphery"; below it the dependency structure is
#: "hierarchical". Roughly matches the empirical large-core threshold in the
#: MacCormack/Baldwin studies.
CORE_PERIPHERY_MIN_RATIO = 0.06

#: Core-periphery role labels (the one place they are spelled).
ROLE_CORE = "core"
ROLE_SHARED = "shared"
ROLE_CONTROL = "control"
ROLE_PERIPHERAL = "peripheral"
ROLE_ORDER: tuple[str, ...] = (ROLE_CORE, ROLE_SHARED, ROLE_CONTROL, ROLE_PERIPHERAL)

#: Architecture-type labels.
ARCH_CORE_PERIPHERY = "core-periphery"
ARCH_HIERARCHICAL = "hierarchical"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class NodeArchitectureRole:
    """Per-service architectural role and its visibility profile.

    ``visibility_fan_out`` is the number of services this one can reach through
    structural dependencies (its row sum in the visibility matrix, counting
    itself); ``visibility_fan_in`` is the number that can reach it (its column
    sum). ``role`` is the core-periphery classification.
    """

    id: str
    repo: str
    name: str
    visibility_fan_in: int
    visibility_fan_out: int
    role: str  # core | shared | control | peripheral

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo": self.repo,
            "name": self.name,
            "visibility_fan_in": self.visibility_fan_in,
            "visibility_fan_out": self.visibility_fan_out,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeArchitectureRole:
        return cls(
            id=data["id"],
            repo=data.get("repo", ""),
            name=data.get("name", data["id"]),
            visibility_fan_in=data.get("visibility_fan_in", 0),
            visibility_fan_out=data.get("visibility_fan_out", 0),
            role=data.get("role", ROLE_PERIPHERAL),
        )


@dataclass
class ArchitectureMetrics:
    """The whole-workspace architecture-complexity read.

    ``propagation_cost`` is the fraction of *other* services the average service
    can reach transitively (0 = fully decoupled, 1 = everything reaches
    everything). ``core_*`` describe the largest cyclic group. ``score`` is the
    deterministic 1-10 roll-up; ``roles`` carries the per-service breakdown.
    """

    node_count: int
    structural_edge_count: int
    propagation_cost: float  # 0-1 fraction
    propagation_cost_pct: float  # percentage, 1 decimal — for display
    core_size: int
    core_ratio: float  # core_size / node_count
    core_members: list[str]
    cycle_count: int
    conformance_violations: int
    architecture_type: str  # core-periphery | hierarchical
    score: float  # 1-10
    roles: list[NodeArchitectureRole] = field(default_factory=list)
    generated_at: str = ""

    def role_breakdown(self) -> dict[str, int]:
        """Count of services per role, in canonical role order."""
        counts = {role: 0 for role in ROLE_ORDER}
        for r in self.roles:
            counts[r.role] = counts.get(r.role, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "structural_edge_count": self.structural_edge_count,
            "propagation_cost": self.propagation_cost,
            "propagation_cost_pct": self.propagation_cost_pct,
            "core_size": self.core_size,
            "core_ratio": self.core_ratio,
            "core_members": self.core_members,
            "cycle_count": self.cycle_count,
            "conformance_violations": self.conformance_violations,
            "architecture_type": self.architecture_type,
            "score": self.score,
            "role_breakdown": self.role_breakdown(),
            "roles": [r.to_dict() for r in self.roles],
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchitectureMetrics:
        return cls(
            node_count=data.get("node_count", 0),
            structural_edge_count=data.get("structural_edge_count", 0),
            propagation_cost=data.get("propagation_cost", 0.0),
            propagation_cost_pct=data.get("propagation_cost_pct", 0.0),
            core_size=data.get("core_size", 0),
            core_ratio=data.get("core_ratio", 0.0),
            core_members=list(data.get("core_members", [])),
            cycle_count=data.get("cycle_count", 0),
            conformance_violations=data.get("conformance_violations", 0),
            architecture_type=data.get("architecture_type", ARCH_HIERARCHICAL),
            score=data.get("score", SCORE_MAX),
            roles=[NodeArchitectureRole.from_dict(r) for r in data.get("roles", [])],
            generated_at=data.get("generated_at", ""),
        )


# ---------------------------------------------------------------------------
# Visibility (reachability) matrix — structural edges only
# ---------------------------------------------------------------------------


def _structural_adjacency(edges: list[SystemEdge]) -> dict[str, set[str]]:
    """Forward dependency adjacency over structural edges: ``source -> {targets}``.

    Direction is the system graph's own: ``source`` depends on / calls
    ``target``. Behavioral co-change edges are excluded — they assert correlated
    change, not a dependency, so they never contribute to reachability.
    """
    adj: dict[str, set[str]] = {}
    for edge in edges:
        if not edge.structural:
            continue
        adj.setdefault(edge.source, set()).add(edge.target)
    return adj


def _reachable(start: str, adj: dict[str, set[str]], node_ids: set[str]) -> set[str]:
    """Cycle-safe transitive closure from *start*, including ``start`` itself.

    A plain DFS with a visited set — the graph is service-granular (tens of
    nodes) so this is cheap, and the visited set makes cycles terminate.
    """
    seen: set[str] = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, set()):
            if nxt in node_ids and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _visibility_matrix(node_ids: list[str], adj: dict[str, set[str]]) -> dict[str, set[str]]:
    """Reachable set (including self) for every node, keyed by node id."""
    valid = set(node_ids)
    return {nid: _reachable(nid, adj, valid) for nid in node_ids}


# ---------------------------------------------------------------------------
# Core (largest cyclic group = largest SCC of the structural graph)
# ---------------------------------------------------------------------------


def _largest_cyclic_core(node_ids: list[str], adj: dict[str, set[str]]) -> list[str]:
    """Return the members of the largest cyclic group, or ``[]`` if acyclic.

    The largest cyclic group is the largest strongly-connected component of the
    structural dependency graph with at least two members (a lone service is not
    a cycle). Delegated to NetworkX — the same SCC routine the rest of the
    codebase relies on; no hand-rolled Tarjan here. Ties broken lexicographically
    on the sorted member list so the result is deterministic.
    """
    try:
        import networkx as nx
    except Exception:  # pragma: no cover - networkx is a hard dependency
        _log.warning("networkx unavailable; skipping core detection", exc_info=True)
        return []

    digraph = nx.DiGraph()
    digraph.add_nodes_from(node_ids)
    for src, targets in adj.items():
        for tgt in targets:
            digraph.add_edge(src, tgt)

    best: list[str] = []
    for component in nx.strongly_connected_components(digraph):
        if len(component) < 2:
            continue  # a single node (even self-looping) is not a cyclic group
        members = sorted(component)
        if len(members) > len(best) or (len(members) == len(best) and members < best):
            best = members
    return best


# ---------------------------------------------------------------------------
# Core-periphery role classification
# ---------------------------------------------------------------------------


def _classify_role(
    vfi: int,
    vfo: int,
    *,
    is_core: bool,
    threshold_fi: float,
    threshold_fo: float,
) -> str:
    """Assign one core-periphery role from a node's visibility profile.

    * **Core** — a member of the largest cyclic group.
    * **Shared** — visibility fan-in at or above the threshold but fan-out below
      it: many services reach it, it reaches few (a widely-used library/utility).
    * **Control** — fan-out at or above but fan-in below: it reaches many, few
      reach it (an orchestrator / entry point).
    * **Peripheral** — neither, including services that touch only themselves.

    The threshold is the core's own visibility floor when a core exists (so a
    non-core node is judged "as visible as the core"), else the median across all
    services (so an acyclic system still splits sensibly).
    """
    if is_core:
        return ROLE_CORE
    # Isolated services (reach and are reached by only themselves) are always
    # peripheral, regardless of thresholds — guards single-service workspaces.
    if vfi <= 1 and vfo <= 1:
        return ROLE_PERIPHERAL

    hi_fi = vfi >= threshold_fi
    hi_fo = vfo >= threshold_fo
    if hi_fi and not hi_fo:
        return ROLE_SHARED
    if hi_fo and not hi_fi:
        return ROLE_CONTROL
    if hi_fi and hi_fo:
        # A would-be core that is not actually cyclic: lean to its dominant axis.
        return ROLE_SHARED if vfi >= vfo else ROLE_CONTROL
    return ROLE_PERIPHERAL


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def architecture_score(
    propagation_cost: float,
    core_ratio: float,
    cycle_count: int,
    conformance_violations: int,
) -> float:
    """Roll the drivers into a deterministic 1-10 score (1 decimal place).

    See the constants block for every weight and cap. Lower coupling, a smaller
    cyclic core, fewer cycles, and fewer rule violations all raise the score.
    """
    penalty = (
        PROPAGATION_COST_WEIGHT * propagation_cost
        + CORE_RATIO_WEIGHT * core_ratio
        + min(CYCLE_PENALTY_CAP, CYCLE_PENALTY_PER * cycle_count)
        + min(VIOLATION_PENALTY_CAP, VIOLATION_PENALTY_PER * conformance_violations)
    )
    return round(_clamp(SCORE_MAX - penalty, SCORE_MIN, SCORE_MAX), 1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_architecture_metrics(
    graph: SystemGraph,
    *,
    conformance_violations: int = 0,
    generated_at: str = "",
) -> ArchitectureMetrics:
    """Compute the workspace architecture metrics from *graph*.

    Pure. ``conformance_violations`` is supplied by the caller (from the already
    computed conformance report) so this module stays independent of rule
    evaluation; pass 0 when no rules are declared. ``generated_at`` is stamped
    through unchanged for the artifact/response.
    """
    node_ids = [n.id for n in graph.nodes]
    n = len(node_ids)

    adj = _structural_adjacency(graph.edges)
    structural_edge_count = sum(len(t) for t in adj.values())

    # Empty / single-service workspaces are vacuously decoupled — no coupling is
    # possible, so the score is the maximum and there is no core.
    if n <= 1:
        roles = [
            NodeArchitectureRole(
                id=node.id,
                repo=node.repo,
                name=node.name,
                visibility_fan_in=1,
                visibility_fan_out=1,
                role=ROLE_PERIPHERAL,
            )
            for node in graph.nodes
        ]
        return ArchitectureMetrics(
            node_count=n,
            structural_edge_count=structural_edge_count,
            propagation_cost=0.0,
            propagation_cost_pct=0.0,
            core_size=0,
            core_ratio=0.0,
            core_members=[],
            cycle_count=0,
            conformance_violations=conformance_violations,
            architecture_type=ARCH_HIERARCHICAL,
            score=SCORE_MAX,
            roles=roles,
            generated_at=generated_at,
        )

    visibility = _visibility_matrix(node_ids, adj)
    vfo = {nid: len(reach) for nid, reach in visibility.items()}
    vfi = {nid: 0 for nid in node_ids}
    for reach in visibility.values():
        for target in reach:
            vfi[target] += 1

    # Propagation cost: off-diagonal density of the visibility matrix — the
    # fraction of *other* services the average service can reach. Excluding the
    # diagonal keeps the metric 0 for a fully decoupled system of any size.
    total_visibility = sum(vfo.values())
    off_diagonal = total_visibility - n  # drop each node's reach-to-self
    denominator = n * (n - 1)
    propagation_cost = off_diagonal / denominator if denominator else 0.0

    core_members = _largest_cyclic_core(node_ids, adj)
    core_set = set(core_members)
    core_size = len(core_members)
    core_ratio = core_size / n if n else 0.0

    # Role thresholds: the core's visibility floor when a core exists, else the
    # median across all services.
    if core_members:
        threshold_fi = float(min(vfi[m] for m in core_members))
        threshold_fo = float(min(vfo[m] for m in core_members))
    else:
        threshold_fi = float(median(vfi[nid] for nid in node_ids))
        threshold_fo = float(median(vfo[nid] for nid in node_ids))

    roles = [
        NodeArchitectureRole(
            id=node.id,
            repo=node.repo,
            name=node.name,
            visibility_fan_in=vfi[node.id],
            visibility_fan_out=vfo[node.id],
            role=_classify_role(
                vfi[node.id],
                vfo[node.id],
                is_core=node.id in core_set,
                threshold_fi=threshold_fi,
                threshold_fo=threshold_fo,
            ),
        )
        for node in graph.nodes
    ]

    cycle_count = len(detect_cycles(graph))
    score = architecture_score(propagation_cost, core_ratio, cycle_count, conformance_violations)
    architecture_type = (
        ARCH_CORE_PERIPHERY if core_ratio >= CORE_PERIPHERY_MIN_RATIO else ARCH_HIERARCHICAL
    )

    return ArchitectureMetrics(
        node_count=n,
        structural_edge_count=structural_edge_count,
        propagation_cost=round(propagation_cost, 4),
        propagation_cost_pct=round(propagation_cost * 100, 1),
        core_size=core_size,
        core_ratio=round(core_ratio, 4),
        core_members=core_members,
        cycle_count=cycle_count,
        conformance_violations=conformance_violations,
        architecture_type=architecture_type,
        score=score,
        roles=roles,
        generated_at=generated_at,
    )
