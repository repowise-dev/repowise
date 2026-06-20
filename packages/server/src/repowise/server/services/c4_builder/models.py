"""Plain dataclasses produced by the C4 builder.

These are framework-agnostic (no Pydantic, no SQLAlchemy) so the builder
can be unit-tested without spinning up a session, and so a future Mermaid
emitter can consume the same data structures.

The server routers wrap these into Pydantic response models (see
``server/schemas.py``); the conversion is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class System:
    """The 'system under design' — the indexed repo as a whole."""

    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class ExternalSystemView:
    id: str            # stable id used in edges; e.g., "ext:react"
    name: str
    display_name: str
    category: str      # framework | service | tool | library
    ecosystem: str
    version: str | None = None
    # Boundary type in {db, network, filesystem, subprocess, lock}; None when
    # the dependency isn't in the io_kind seed table (renders untyped).
    io_kind: str | None = None


@dataclass(frozen=True)
class Container:
    """A deployable / runnable unit. Typically a workspace package, or a
    top-level directory in a non-monorepo. ``path`` is repo-relative.
    """

    id: str            # "pkg:packages/core"
    name: str
    path: str
    language: str
    file_count: int
    symbol_count: int
    hotspot_count: int = 0
    dead_count: int = 0


@dataclass(frozen=True)
class Component:
    """A sub-module inside a container — top-level child directory, or the
    synthetic ``_root`` group for files at the container root.
    """

    id: str            # "cmp:packages/core/ingestion"
    name: str
    path: str          # repo-relative
    container_id: str
    file_count: int
    symbol_count: int


@dataclass(frozen=True)
class Relation:
    """A typed edge between any two C4 boxes (container ↔ container,
    container ↔ external, component ↔ component, component ↔ external).
    """

    source_id: str
    target_id: str
    label: str = ""
    edge_count: int = 1
    edge_types: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class C4L1:
    system: System
    people: list[Person]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]


@dataclass(frozen=True)
class C4L2:
    containers: list[Container]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]


@dataclass(frozen=True)
class C4L3:
    container: Container
    components: list[Component]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]


# ---------------------------------------------------------------------------
# Architecture view (unified model)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchSubGroup:
    """A curated sub-group inside a layer (drill-down tier between layer
    cards and file cards). Produced by the KG curation pass."""

    id: str
    name: str
    node_ids: list[str]


@dataclass(frozen=True)
class ArchLayer:
    id: str
    name: str
    description: str
    node_ids: list[str]
    file_count: int
    complexity_distribution: dict[str, int]
    health_score: float | None
    sub_groups: list[ArchSubGroup] = field(default_factory=list)
    display_order: int = 0


@dataclass(frozen=True)
class ArchNode:
    id: str
    node_type: str
    name: str
    file_path: str | None
    line_range: tuple[int, int] | None
    summary: str
    complexity: str
    tags: list[str]
    language: str | None
    pagerank: float
    pagerank_percentile: float
    betweenness: float
    in_degree: int
    out_degree: int
    community_id: int | None
    is_entry_point: bool
    is_test: bool
    is_hotspot: bool
    is_dead: bool
    has_doc: bool
    primary_owner: str | None
    primary_owner_pct: float | None
    bus_factor: int | None


@dataclass(frozen=True)
class ArchEdge:
    source: str
    target: str
    edge_type: str
    direction: str
    weight: float
    confidence: float


@dataclass(frozen=True)
class ArchTourStep:
    order: int
    title: str
    description: str
    node_ids: list[str]
    # Curated, layer-aware fields (None/empty for legacy LLM tours).
    target_path: str | None = None
    layer_id: str | None = None
    reason: str = ""
    depth: int | None = None
    kind: str = ""
    page_type: str | None = None


@dataclass(frozen=True)
class ArchitectureView:
    project_name: str
    project_description: str
    layers: list[ArchLayer]
    nodes: list[ArchNode]
    edges: list[ArchEdge]
    tour: list[ArchTourStep]
    total_files: int
    total_symbols: int
    total_edges: int
    languages: list[str]
    frameworks: list[str]
    external_systems: list[ExternalSystemView]
    # Curated, ranked entry points (repo-relative paths; empty when uncurated).
    entry_points: list[str] = field(default_factory=list)
    entry_candidates: list[str] = field(default_factory=list)
