"""System graph — the one normalized cross-repo structure every view reads.

Workspace mode already detects contracts, package dependencies, and file-level
co-changes, but each is a flat list. This module folds them into a single
service-granular graph:

* **Nodes are services**, not repos. A monorepo with three service boundaries
  yields three nodes; the repo is a grouping attribute on each node. A repo with
  no detected sub-boundary collapses to one repo-root node.
* **Edges are typed and honest.** Every edge carries its ``kind`` (http / grpc /
  event / package / co_change / db), ``match_type`` (exact / candidate / manual /
  inferred), a ``confidence``, a ``weight`` (how many underlying contracts /
  co-changes / deps it aggregates), and ``contract_refs`` back-pointers so any
  consumer can drill from an edge to its evidence. Structural edges (contracts,
  package deps) are flagged distinctly from behavioral co-change edges.

Edge direction is uniform: **source depends on / calls target.** A consumer
points to the provider it calls; a dependent repo points to the repo it imports.

The graph is built once during ``repowise update --workspace`` (where contracts,
the cross-repo overlay, and service boundaries are all in hand) and persisted to
``.repowise-workspace/system_graph.json``. The map (Phase 2), blast radius, the
DSM, and the MCP/CLI surfaces are all *views* over this one artifact — none of
them re-derives the graph.

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
from typing import Any

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
    "topic": "event",
}

#: All edge kinds the graph can carry. ``db`` is reserved for a future
#: shared-table transport; the taxonomy is fixed now so views render consistently.
EDGE_KINDS: tuple[str, ...] = ("http", "grpc", "event", "package", "co_change", "db")

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
_STRUCTURAL_KINDS: frozenset[str] = frozenset({"http", "grpc", "event", "package", "db"})


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

    def to_dict(self) -> dict[str, Any]:
        return {
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "match_type": self.match_type,
            "confidence": self.confidence,
            "weight": self.weight,
            "structural": self.structural,
            "contract_refs": self.contract_refs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemEdge:
        return cls(
            id=data["id"],
            source=data["source"],
            target=data["target"],
            kind=data["kind"],
            match_type=data.get("match_type", "exact"),
            confidence=data.get("confidence", 0.0),
            weight=data.get("weight", 1),
            structural=data.get("structural", True),
            contract_refs=data.get("contract_refs", []),
        )


@dataclass
class SystemGraph:
    """The versioned cross-repo structure: nodes, typed edges, diagnostics."""

    version: int = 1
    generated_at: str = ""
    nodes: list[SystemNode] = field(default_factory=list)
    edges: list[SystemEdge] = field(default_factory=list)
    diagnostics: ExtractionDiagnostics = field(default_factory=ExtractionDiagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "diagnostics": self.diagnostics.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemGraph:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            nodes=[SystemNode.from_dict(n) for n in data.get("nodes", [])],
            edges=[SystemEdge.from_dict(e) for e in data.get("edges", [])],
            diagnostics=ExtractionDiagnostics.from_dict(data.get("diagnostics", {})),
        )


# ---------------------------------------------------------------------------
# Graph assembly (pure)
# ---------------------------------------------------------------------------


def _node_id(repo: str, service_path: str | None) -> str:
    return repo if service_path is None else f"{repo}::{service_path}"


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
        service = (
            assign_service(file_path, self._boundaries.get(repo, []))
            if file_path is not None
            else None
        )
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
            )
            return
        existing.weight += 1
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
            if e.structural and e.kind != "package":
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
    version: int = 1,
    generated_at: str = "",
) -> SystemGraph:
    """Assemble the service-granular system graph (pure, no I/O).

    Nodes are derived from each contract's service boundary; edges from matched
    contract links (consumer→provider), package dependencies (dependent→
    dependency), and cross-repo co-changes (undirected, behavioral).
    """
    if diagnostics is None:
        diagnostics = build_diagnostics(contracts, links)

    builder = _GraphBuilder(boundaries_by_repo)

    # 1. Nodes + provider/consumer counts from every contract.
    for c in contracts:
        builder.count_contract(c)

    # 2. Contract edges — the consumer calls the provider.
    for lk in links:
        source = builder.resolve_node(lk.consumer_repo, lk.consumer_file)
        target = builder.resolve_node(lk.provider_repo, lk.provider_file)
        builder.add_edge(
            source=source,
            target=target,
            kind=edge_kind_for_contract_type(lk.contract_type),
            match_type=lk.match_type,
            confidence=lk.confidence,
            ref=lk.contract_id,
        )

    # 3. Package-dependency edges — the dependent repo imports the dependency.
    for dep in overlay.package_deps:
        source = builder.resolve_node(dep.source_repo, dep.source_manifest)
        target = builder.resolve_node(dep.target_repo, None)
        builder.add_edge(
            source=source,
            target=target,
            kind="package",
            match_type="exact",
            confidence=1.0,
            ref=f"{dep.kind}:{dep.source_manifest}",
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
        )

    nodes, edges = builder.finalize(diagnostics)
    return SystemGraph(
        version=version,
        generated_at=generated_at,
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_system_graph(graph: SystemGraph, workspace_root: Path) -> Path:
    """Write the system graph to ``.repowise-workspace/system_graph.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / SYSTEM_GRAPH_FILENAME
    out_path.write_text(
        json.dumps(graph.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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
) -> SystemGraph:
    """Build and persist the system graph from the latest contracts + overlay.

    Called from ``run_cross_repo_hooks`` after contract extraction and
    cross-repo analysis. Boundary detection (a filesystem walk) runs off-thread.
    """
    boundaries_by_repo = await asyncio.to_thread(
        _detect_boundaries_by_repo, ws_config, workspace_root
    )

    diagnostics = build_diagnostics(store.contracts, store.contract_links)
    graph = build_system_graph(
        store.contracts,
        store.contract_links,
        overlay,
        boundaries_by_repo,
        diagnostics,
        generated_at=datetime.now(UTC).isoformat(),
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
