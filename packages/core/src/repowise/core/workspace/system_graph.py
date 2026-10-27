"""System graph — the one normalized cross-repo structure every view reads.

Workspace mode already detects contracts, package dependencies, and file-level
co-changes, but each is a flat list. This module folds them into a single
service-granular graph:

* **Nodes are services**, not repos. A monorepo with three service boundaries
  yields three nodes; the repo is a grouping attribute on each node. A repo with
  no detected sub-boundary collapses to one repo-root node.
* **Edges are typed and honest.** Every edge carries its ``kind`` (http / grpc /
  event / package / co_change / db), ``match_type`` (exact / candidate / manual /
  inferred), the ``strategy`` that made its strongest link (see :mod:`.origins`;
  None on a co-change edge), a ``confidence``, a ``weight`` (how many underlying contracts /
  co-changes / deps it aggregates), and ``contract_refs`` back-pointers so any
  consumer can drill from an edge to its evidence. Structural edges (contracts,
  package deps) are flagged distinctly from behavioral co-change edges.

Edge direction is uniform: **source depends on / calls target.** A consumer
points to the provider it calls; a dependent repo points to the repo it imports.

The graph is built once during ``repowise update --workspace`` (where contracts,
the cross-repo overlay, and service boundaries are all in hand) and persisted to
``.repowise-workspace/system_graph.json``. The map (Phase 2), blast radius, the
DSM, and the MCP/CLI surfaces are all *views* over this one artifact — none of
them re-derives the graph. Each reads it through :meth:`SystemGraph.adjacency`,
the one place that decides which edges a dependency walk follows.

Pure and I/O-free except for the thin ``save_system_graph`` / ``load_system_graph``
helpers and the ``run_system_graph_build`` orchestrator at the bottom.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.fsutils import atomic_write_text
from repowise.core.workspace.config import (
    WORKSPACE_DATA_DIR,
    WorkspaceConfig,
    ensure_workspace_data_dir,
)
from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.diagnostics import ExtractionDiagnostics, build_diagnostics
from repowise.core.workspace.extractors.service_boundary import (
    ServiceBoundary,
    assign_service,
    detect_service_boundaries,
)
from repowise.core.workspace.grouping import ServiceGroup, group_services
from repowise.core.workspace.origins import LinkStrategy, legacy_strategy

if TYPE_CHECKING:
    from collections.abc import Iterable

_log = logging.getLogger("repowise.workspace.system_graph")

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

SYSTEM_GRAPH_FILENAME = "system_graph.json"

#: Maps a contract's ``contract_type`` to the system-graph edge ``kind``. New
#: contract transports add one line here, never an ``if/elif`` branch downstream.
_CONTRACT_TYPE_TO_EDGE_KIND: dict[str, str] = {
    "http": "http",
    "grpc": "grpc",
    "socket": "socket",
    "topic": "event",
    "data": "db",
    # A package boundary is exactly what the existing "package" kind means,
    # so the graph, the conformance rules and the map need no new taxonomy.
    "code": "package",
}

#: Every edge kind the graph can carry, mapped to its family: ``runtime`` (a
#: call or message at run time), ``data`` (a shared store; ``db`` carries the
#: ``data`` contracts), ``build`` (a manifest or package reference) or
#: ``history`` (correlated change). The one owner of kind-to-family: views read
#: the ``family`` each edge carries, and grouping clusters on it.
EDGE_KIND_FAMILY: dict[str, str] = {
    "http": "runtime",
    "grpc": "runtime",
    "socket": "runtime",
    "event": "runtime",
    "db": "data",
    "package": "build",
    "co_change": "history",
}

#: All edge kinds the graph can carry; the taxonomy is fixed so views render consistently.
EDGE_KINDS: tuple[str, ...] = ("http", "grpc", "socket", "event", "package", "co_change", "db")

#: Families in display order.
EDGE_FAMILIES: tuple[str, ...] = ("runtime", "data", "build", "history")

#: ``match_type`` precedence when several links collapse onto one edge — the most
#: authoritative wins for display. Higher is stronger.
_MATCH_TYPE_RANK: dict[str, int] = {
    "exact": 3,
    "manual": 2,
    "candidate": 1,
    "inferred": 0,
}

#: Cap on the number of evidence back-pointers stored per edge. ``weight`` always
#: reflects the true contributor count; this only bounds the drill-down list so a
#: pathological co-change fan-out can't bloat the artifact.
MAX_EDGE_REFS = 50

#: Structural edges assert a real dependency; behavioral (co-change) edges only
#: assert correlated change. Kept distinct so reachability and the map never
#: conflate "call each other" with "change together".
_STRUCTURAL_KINDS: frozenset[str] = frozenset(
    kind for kind, family in EDGE_KIND_FAMILY.items() if family != "history"
)

#: Structural kinds whose dependency is asynchronous: the subscriber depends on
#: the message shape, not on the publisher answering. Still a dependency (a
#: cycle through one couples schemas and deploy order), so every view walks it;
#: this set is what lets a view say which legs of a path are async.
ASYNC_EDGE_KINDS: frozenset[str] = frozenset({"event"})


def edge_family(kind: str) -> str:
    """The family of an edge *kind*.

    A kind missing from :data:`EDGE_KIND_FAMILY` is ``history``: the builder
    already treats a kind outside :data:`_STRUCTURAL_KINDS` as non-structural
    (undirected, never walked), so it is never drawn or grouped as a dependency.
    """
    return EDGE_KIND_FAMILY.get(kind, "history")


def edge_kind_for_contract_type(contract_type: str) -> str:
    """Map a contract type to its edge kind, falling back to the type itself."""
    return _CONTRACT_TYPE_TO_EDGE_KIND.get(contract_type, contract_type)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SystemNode:
    """A service in the workspace (or a repo-root node when undivided)."""

    id: str  # "repo" or "repo::service/path"
    repo: str  # repo alias — the grouping attribute
    service_path: str | None  # None for a whole-repo node
    name: str  # display name (service dir basename, or repo alias)
    kind: str = "service"  # service | frontend | worker | library | external
    provider_count: int = 0
    consumer_count: int = 0
    contract_types: list[str] = field(default_factory=list)
    is_orphan_provider: bool = False  # exposes providers nobody consumes
    is_orphan_consumer: bool = False  # consumes contracts that never matched
    is_isolated: bool = False  # participates in no edges
    #: Id of the :class:`.grouping.ServiceGroup` this service belongs to; None
    #: when ungrouped, which every node is on an estate too small to group.
    group: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "repo": self.repo,
            "service_path": self.service_path,
            "name": self.name,
            "kind": self.kind,
            "provider_count": self.provider_count,
            "consumer_count": self.consumer_count,
            "contract_types": self.contract_types,
            "is_orphan_provider": self.is_orphan_provider,
            "is_orphan_consumer": self.is_orphan_consumer,
            "is_isolated": self.is_isolated,
        }
        if self.group is not None:
            data["group"] = self.group
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemNode:
        return cls(
            id=data["id"],
            repo=data["repo"],
            service_path=data.get("service_path"),
            name=data.get("name", data["id"]),
            kind=data.get("kind", "service"),
            provider_count=data.get("provider_count", 0),
            consumer_count=data.get("consumer_count", 0),
            contract_types=data.get("contract_types", []),
            is_orphan_provider=data.get("is_orphan_provider", False),
            is_orphan_consumer=data.get("is_orphan_consumer", False),
            is_isolated=data.get("is_isolated", False),
            group=data.get("group"),
        )


@dataclass
class SystemEdge:
    """A typed, directed relationship between two services (source → target)."""

    id: str
    source: str  # node id that depends on / calls the target
    target: str  # node id that is depended on / called
    kind: str  # one of EDGE_KINDS
    match_type: str  # exact | candidate | manual | inferred
    confidence: float
    weight: int  # number of underlying contracts / co-changes / deps
    structural: bool  # True for contract/package, False for co-change
    contract_refs: list[str] = field(default_factory=list)
    #: The :class:`.origins.LinkStrategy` of the link that gave the edge its
    #: confidence. None on a co-change edge, which no link made.
    strategy: str | None = None

    @property
    def family(self) -> str:
        """``runtime`` | ``data`` | ``build`` | ``history``; see :func:`edge_family`."""
        return edge_family(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "family": self.family,
            "match_type": self.match_type,
            "confidence": self.confidence,
            "weight": self.weight,
            "structural": self.structural,
            "contract_refs": self.contract_refs,
            "strategy": self.strategy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemEdge:
        match_type = data.get("match_type", "exact")
        strategy = data.get("strategy")
        if strategy is None and data.get("kind") == "package":
            strategy = LinkStrategy.MANIFEST  # a graph written before strategies existed
        elif strategy is None and data.get("kind") != "co_change":
            strategy = legacy_strategy(match_type)
        return cls(
            id=data["id"],
            source=data["source"],
            target=data["target"],
            kind=data["kind"],
            match_type=match_type,
            confidence=data.get("confidence", 0.0),
            weight=data.get("weight", 1),
            structural=data.get("structural", True),
            contract_refs=data.get("contract_refs", []),
            strategy=strategy,
        )


@dataclass
class SystemGraph:
    """The versioned cross-repo structure: nodes, typed edges, diagnostics."""

    version: int = 2
    generated_at: str = ""
    nodes: list[SystemNode] = field(default_factory=list)
    edges: list[SystemEdge] = field(default_factory=list)
    diagnostics: ExtractionDiagnostics = field(default_factory=ExtractionDiagnostics)
    repo_provenance: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Service groups (see :mod:`.grouping`); empty on an estate too small to group.
    groups: list[ServiceGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "generated_at": self.generated_at,
            "repo_provenance": self.repo_provenance,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "diagnostics": self.diagnostics.to_dict(),
        }
        if self.groups:
            data["groups"] = [g.to_dict() for g in self.groups]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemGraph:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            repo_provenance=data.get("repo_provenance", {}),
            nodes=[SystemNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[SystemEdge.from_dict(e) for e in data.get("edges", [])],
            diagnostics=ExtractionDiagnostics.from_dict(data.get("diagnostics", {})),
            groups=[ServiceGroup.from_dict(g) for g in data.get("groups", [])],
        )

    def adjacency(
        self, *, behavioral: bool = False, impact: bool = False
    ) -> dict[str, dict[str, list[SystemEdge]]]:
        """The graph as ``node -> {neighbour: [edges]}``: the one view every walk reads.

        Default: dependency direction (``source`` depends on ``target``) over
        structural edges, which is what cycles, the architecture metrics and
        reachability walk. *impact* flips it to "a change to the key reaches
        the neighbour" (``target -> source``), the blast-radius direction.
        *behavioral* adds co-change edges, both ways, since they are symmetric.

        Parallel edges between one pair (an http and an event edge, say) stay
        separate in the list, in graph order, so a caller can tell a sync leg
        from an async one by each edge's ``kind`` (:data:`ASYNC_EDGE_KINDS`).
        """
        adj: dict[str, dict[str, list[SystemEdge]]] = {}

        def link(a: str, b: str, edge: SystemEdge) -> None:
            adj.setdefault(a, {}).setdefault(b, []).append(edge)

        for edge in self.edges:
            if edge.structural:
                if impact:
                    link(edge.target, edge.source, edge)
                else:
                    link(edge.source, edge.target, edge)
            elif behavioral:
                link(edge.source, edge.target, edge)
                link(edge.target, edge.source, edge)
        return adj


# ---------------------------------------------------------------------------
# Graph assembly (pure)
# ---------------------------------------------------------------------------


def _node_id(repo: str, service_path: str | None) -> str:
    return repo if service_path is None else f"{repo}::{service_path}"


def resolve_service_id(
    repo: str,
    file_path: str | None,
    boundaries_by_repo: dict[str, list[ServiceBoundary]],
) -> str:
    """The node id *file_path* in *repo* belongs to: its deepest service, else the repo root.

    ``file_path is None`` resolves to the repo-root node (repo-level facts
    like a package dependency on a whole repo). The builder and every read
    that maps a file to a service call this, so they cannot disagree.
    """
    return _node_id(repo, _service_path(repo, file_path, boundaries_by_repo))


def _service_path(
    repo: str, file_path: str | None, boundaries_by_repo: dict[str, list[ServiceBoundary]]
) -> str | None:
    if file_path is None:
        return None
    return assign_service(file_path, boundaries_by_repo.get(repo, []))


def boundaries_from_nodes(nodes: Iterable[dict[str, Any]]) -> dict[str, list[ServiceBoundary]]:
    """The service boundaries a stored graph's nodes stand for, by repo.

    The graph does not persist boundary detection, but it does not need to:
    every link end the builder resolved became a node, so the deepest boundary
    holding that end is among the nodes, and longest-prefix matching against
    the nodes alone resolves the same end to the same id.
    That holds for nodes and links written by the same build, which a
    workspace update always does.
    """
    out: dict[str, list[ServiceBoundary]] = {}
    for node in nodes:
        path = node.get("service_path")
        if path:
            out.setdefault(node["repo"], []).append(
                ServiceBoundary(service_path=path, service_name=path.rsplit("/", 1)[-1])
            )
    return out


class _GraphBuilder:
    """Accumulates nodes and aggregated edges as contracts/edges are walked.

    Edges that share ``(source, target, kind)`` collapse into one weighted edge:
    weight counts contributors, confidence keeps the max, match_type keeps the
    most authoritative, and refs accumulate (bounded by :data:`MAX_EDGE_REFS`).
    """

    def __init__(self, boundaries_by_repo: dict[str, list[ServiceBoundary]]) -> None:
        self._boundaries = boundaries_by_repo
        self._nodes: dict[str, SystemNode] = {}
        self._edges: dict[tuple[str, str, str], SystemEdge] = {}

    # -- nodes ----------------------------------------------------------------

    def resolve_node(self, repo: str, file_path: str | None) -> str:
        """Return the node id for *file_path* in *repo*, creating it if needed.

        ``file_path is None`` resolves to the repo-root node (repo-level facts
        like a package dependency on a whole repo).
        """
        service = _service_path(repo, file_path, self._boundaries)
        nid = _node_id(repo, service)
        if nid not in self._nodes:
            self._nodes[nid] = SystemNode(
                id=nid,
                repo=repo,
                service_path=service,
                name=service.rsplit("/", 1)[-1] if service else repo,
            )
        return nid

    def count_contract(self, contract: Contract) -> None:
        nid = self.resolve_node(contract.repo, contract.file_path)
        node = self._nodes[nid]
        if contract.role == "provider":
            node.provider_count += 1
        elif contract.role == "consumer":
            node.consumer_count += 1
        if contract.contract_type not in node.contract_types:
            node.contract_types.append(contract.contract_type)

    # -- edges ----------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        match_type: str,
        confidence: float,
        ref: str,
        strategy: str | None,
    ) -> None:
        if source == target:
            return  # intra-node calls aren't system-level edges
        structural = kind in _STRUCTURAL_KINDS
        if not structural:
            # Co-change is symmetric — canonicalize direction so A~B and B~A
            # collapse to a single undirected edge.
            source, target = sorted((source, target))
        key = (source, target, kind)
        existing = self._edges.get(key)
        if existing is None:
            self._edges[key] = SystemEdge(
                id=f"{source}->{target}:{kind}",
                source=source,
                target=target,
                kind=kind,
                match_type=match_type,
                confidence=round(confidence, 3),
                weight=1,
                structural=structural,
                contract_refs=[ref] if ref else [],
                strategy=strategy,
            )
            return
        existing.weight += 1
        if round(confidence, 3) > existing.confidence:
            existing.strategy = strategy
        existing.confidence = round(max(existing.confidence, confidence), 3)
        if _MATCH_TYPE_RANK.get(match_type, 0) > _MATCH_TYPE_RANK.get(existing.match_type, 0):
            existing.match_type = match_type
        if ref and len(existing.contract_refs) < MAX_EDGE_REFS:
            existing.contract_refs.append(ref)

    # -- finalize -------------------------------------------------------------

    def finalize(
        self, diagnostics: ExtractionDiagnostics
    ) -> tuple[list[SystemNode], list[SystemEdge]]:
        edges = list(self._edges.values())

        endpoints: set[str] = set()
        contract_targets: set[str] = set()  # nodes consumed as a provider
        contract_sources: set[str] = set()  # nodes that call out as a consumer
        for e in edges:
            endpoints.add(e.source)
            endpoints.add(e.target)
            # A `package` edge is a manifest dependency, which says nothing
            # about whether a contract was consumed — unless a `code` contract
            # link built it, which `contract_refs` is what distinguishes.
            if e.structural and (e.kind != "package" or e.contract_refs):
                contract_targets.add(e.target)
                contract_sources.add(e.source)

        for node in self._nodes.values():
            node.contract_types = sorted(node.contract_types)
            node.is_isolated = node.id not in endpoints
            node.is_orphan_provider = node.provider_count > 0 and node.id not in contract_targets
            node.is_orphan_consumer = node.consumer_count > 0 and node.id not in contract_sources

        nodes = sorted(self._nodes.values(), key=lambda n: n.id)
        edges.sort(key=lambda e: e.id)
        return nodes, edges


def build_system_graph(
    contracts: list[Contract],
    links: list[ContractLink],
    overlay: CrossRepoOverlay,
    boundaries_by_repo: dict[str, list[ServiceBoundary]],
    diagnostics: ExtractionDiagnostics | None = None,
    *,
    version: int = 2,
    generated_at: str = "",
    repo_provenance: dict[str, dict[str, str]] | None = None,
    service_links: list[ContractLink] | None = None,
    repo_tags: dict[str, list[str]] | None = None,
) -> SystemGraph:
    """Assemble the service-granular system graph (pure, no I/O).

    Nodes are derived from each contract's service boundary; edges from matched
    contract links (consumer→provider), service-level links (a consumer to the
    repo its host names, no route known; :attr:`.origins.LinkStrategy.HOST_ONLY`),
    package dependencies (dependent→dependency), and cross-repo co-changes
    (undirected, behavioral). Services are then grouped (:mod:`.grouping`),
    reading declared groups from *repo_tags* (repo alias to its tags).
    """
    if diagnostics is None:
        diagnostics = build_diagnostics(contracts, links)

    builder = _GraphBuilder(boundaries_by_repo)

    # 1. Nodes + provider/consumer counts from every contract.
    for c in contracts:
        builder.count_contract(c)

    # 2. Contract edges — the consumer calls the provider (or, for a service
    # link, the provider's repo: no provider file, so its repo-root node).
    for lk in [*links, *(service_links or ())]:
        source = builder.resolve_node(lk.consumer_repo, lk.consumer_file)
        target = builder.resolve_node(lk.provider_repo, lk.provider_file or None)
        builder.add_edge(
            source=source,
            target=target,
            kind=edge_kind_for_contract_type(lk.contract_type),
            match_type=lk.match_type,
            confidence=lk.confidence,
            ref=lk.contract_id,
            strategy=lk.strategy,
        )

    # 3. Package-dependency edges — the dependent repo imports the dependency.
    for dep in overlay.package_deps:
        source = builder.resolve_node(dep.source_repo, dep.source_manifest)
        target = builder.resolve_node(dep.target_repo, dep.target_manifest or None)
        reference = ":".join(
            part for part in (dep.kind, dep.target_package, dep.source_manifest) if part
        )
        builder.add_edge(
            source=source,
            target=target,
            kind="package",
            match_type="exact",
            confidence=1.0,
            ref=reference,
            strategy=LinkStrategy.MANIFEST,
        )

    # 4. Co-change edges — behavioral, undirected, lower trust.
    for cc in overlay.co_changes:
        source = builder.resolve_node(cc.source_repo, cc.source_file)
        target = builder.resolve_node(cc.target_repo, cc.target_file)
        builder.add_edge(
            source=source,
            target=target,
            kind="co_change",
            match_type="inferred",
            confidence=cc.strength,
            ref=f"{cc.source_file}~{cc.target_file}",
            strategy=None,
        )

    nodes, edges = builder.finalize(diagnostics)
    assignment, groups = group_services(nodes, edges, repo_tags)
    for node in nodes:
        node.group = assignment.get(node.id)
    return SystemGraph(
        version=version,
        generated_at=generated_at,
        repo_provenance=repo_provenance or {},
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
        groups=groups,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_system_graph(graph: SystemGraph, workspace_root: Path) -> Path:
    """Write the system graph to ``.repowise-workspace/system_graph.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / SYSTEM_GRAPH_FILENAME
    # Atomic: the MCP enricher reads these artifacts from a separate
    # process and must never observe a half-written file.
    atomic_write_text(out_path, json.dumps(graph.to_dict(), indent=2, ensure_ascii=False))
    return out_path


def load_system_graph(workspace_root: Path) -> SystemGraph | None:
    """Load the system graph, or ``None`` if missing/unparseable."""
    path = workspace_root / WORKSPACE_DATA_DIR / SYSTEM_GRAPH_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SystemGraph.from_dict(data)
    except Exception:
        _log.warning("Failed to load system graph from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _detect_boundaries_by_repo(
    ws_config: WorkspaceConfig, workspace_root: Path
) -> dict[str, list[ServiceBoundary]]:
    """Detect service boundaries for every indexed repo in the workspace."""
    boundaries: dict[str, list[ServiceBoundary]] = {}
    for entry in ws_config.repos:
        resolved = (workspace_root / entry.path).resolve()
        if resolved.is_dir() and (resolved / ".repowise").is_dir():
            boundaries[entry.alias] = detect_service_boundaries(resolved)
    return boundaries


async def run_system_graph_build(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    store: ContractStore,
    overlay: CrossRepoOverlay,
    boundaries_by_repo: dict[str, list[ServiceBoundary]] | None = None,
) -> SystemGraph:
    """Build and persist the system graph from the latest contracts + overlay.

    Called from ``run_cross_repo_hooks`` after contract extraction and
    cross-repo analysis. Boundary detection (a filesystem walk) runs off-thread.

    *boundaries_by_repo* is the map ``run_cross_repo_hooks`` already computed
    for contract extraction. Both consumers need the same answer, so passing it
    in halves the boundary walks per update; None re-detects for direct callers.
    """
    if boundaries_by_repo is None:
        boundaries_by_repo = await asyncio.to_thread(
            _detect_boundaries_by_repo, ws_config, workspace_root
        )

    diagnostics = build_diagnostics(store.contracts, store.contract_links, store.extraction_stats)
    graph = build_system_graph(
        store.contracts,
        store.contract_links,
        overlay,
        boundaries_by_repo,
        diagnostics,
        generated_at=datetime.now(UTC).isoformat(),
        repo_provenance=store.repo_provenance,
        service_links=store.service_links,
        repo_tags={entry.alias: entry.tags for entry in ws_config.repos},
    )

    out_path = save_system_graph(graph, workspace_root)
    _log.info(
        "System graph built: %d nodes, %d edges, %d unmatched consumers → %s",
        len(graph.nodes),
        len(graph.edges),
        len(diagnostics.unmatched_consumers),
        out_path,
    )
    return graph
