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
    # How this actor enters the system: cli | api | scheduler | developer |
    # user. Drives the L1 actor icon; "user" is the generic fallback.
    kind: str = "user"


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
    """A sub-module inside a container — a meaningful child directory, or the
    synthetic ``(root)`` group for files that sit at the container root.
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
    # Qualitative coupling strength derived from ``edge_count``:
    # loose | moderate | tight. Empty on synthetic edges (e.g. L1 actor->system).
    coupling: str = ""


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


@dataclass(frozen=True)
class BoxSignals:
    """What we know about a container or component beyond its shape.

    C4 has no vocabulary for any of this — which is the point. Carried
    per-box so an exporter can attach it without going back to the database,
    and every field is optional because health data is sparse: ``None`` means
    "not known", which must not be rendered as a real score of zero.
    """

    hotspot_count: int = 0
    dead_count: int = 0
    #: Names of the curated layers this box's files belong to. A box can span
    #: several, so this is a list rather than one label.
    layers: tuple[str, ...] = ()
    #: The person owning the most files in this box, and their share of them.
    primary_owner: str | None = None
    primary_owner_pct: float | None = None
    #: Lowest bus factor among the box's files — the worst case, not the mean,
    #: because one unowned file is the risk a reader cares about.
    min_bus_factor: int | None = None


@dataclass(frozen=True)
class TourStep:
    """One step of the curated reading order.

    Structurizr has no concept of a guided tour, so this rides along as a
    comment. Kept as data rather than a formatted string so the emitter
    decides how it reads.
    """

    order: int
    title: str
    description: str = ""
    reason: str = ""
    target_path: str | None = None
    layer_name: str | None = None


@dataclass(frozen=True)
class C4Model:
    """Every C4 level at once, built from one pass over the graph.

    The dashboard views (``C4L1``/``C4L2``/``C4L3``) each answer one question
    about one level. Anything that has to walk the whole model — an export,
    say — would otherwise call ``build_l3`` per container and re-read the
    graph each time. This carries all of it, and nothing below it touches a
    session, so an emitter is a pure function of this value.

    ``components_by_container`` is empty when components were not requested.
    ``component_relations`` roll edges up to component granularity across
    every container, so a cross-container edge names the real component on
    both ends rather than collapsing into its container.
    """

    system: System
    people: list[Person]
    containers: list[Container]
    components_by_container: dict[str, list[Component]]
    external_systems: list[ExternalSystemView]
    container_relations: list[Relation]
    component_relations: list[Relation]
    #: Actor→system edges. Their own field rather than part of
    #: ``container_relations`` because they belong to no level's box graph and
    #: must survive every include/exclude flag — a person with no edge to the
    #: system is not drawn at all in a context view.
    actor_relations: list[Relation] = field(default_factory=list)
    #: Health, ownership and layer membership, keyed by container/component id.
    #: Empty when the repo has none of it — an export must degrade to plain C4
    #: rather than emitting placeholders.
    box_signals: dict[str, BoxSignals] = field(default_factory=dict)
    #: The curated reading order, in step order. Empty when there is no tour.
    tour: list[TourStep] = field(default_factory=list)


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
