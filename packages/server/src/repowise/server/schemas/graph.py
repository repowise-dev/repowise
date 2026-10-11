"""Graph-export response models (file/symbol graph, module graph, ego graph,
dead-code graph, hot-files graph and the community super-node graph)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .git import GitMetadataResponse


class GraphNodeResponse(BaseModel):
    node_id: str
    node_type: str
    language: str
    symbol_count: int
    pagerank: float
    betweenness: float
    community_id: int
    is_test: bool = False
    is_entry_point: bool = False
    has_doc: bool = False
    # Cross-link signals (populated by services.node_signals.collect_node_signals)
    is_hotspot: bool = False
    churn_percentile: float | None = None
    is_dead: bool = False
    dead_confidence: float | None = None
    has_decision: bool = False
    primary_owner: str | None = None


class GraphEdgeResponse(BaseModel):
    source: str
    target: str
    imported_names: list[str]
    # Both already exist and are fully populated on GraphEdge; they were simply
    # never serialized. Without edge_type the client can only infer edge kind
    # from `imported_names` being empty, which lumps every `defines` / `calls` /
    # `co_changes` / `has_method` row into one "dynamic" bucket. Optional so
    # every other consumer of this model gains them additively.
    edge_type: str | None = None
    confidence: float | None = None


class GraphExportResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    links: list[GraphEdgeResponse]
    # When the graph is too large to return in full, the server caps the response
    # (PageRank fill + reserved slots for dead/hot/flow nodes). Clients should
    # surface a banner.
    truncated: bool = False
    total_node_count: int | None = None
    # Signal-overlay counts: repo-wide totals vs how many made it into this
    # response. Lets clients render "12 of 37 dead files in view" and
    # distinguish "none in repo" from "none in view". None on endpoints that
    # don't compute them.
    dead_total: int | None = None
    dead_in_view: int | None = None
    hot_total: int | None = None
    hot_in_view: int | None = None


# Architecture / community super-node graph
class PopulationBreakdown(BaseModel):
    """What the map is counting.

    Every count in the payload is over ``visible``. The category totals are
    reported whether or not included, so a client can offer "show tests (N)".
    """

    total: int
    visible: int
    tests: int = 0
    examples: int = 0
    docs: int = 0
    include_tests: bool = False
    include_examples: bool = False
    include_docs: bool = False


class UnclusteredFiles(BaseModel):
    """Visible files below ``min_members``; almost all have no dependency edge.

    ``files`` is the head by PageRank, capped.
    """

    file_count: int
    files: list[str] = []


class ArchitectureNodeResponse(BaseModel):
    community_id: int
    label: str
    #: Decays with size; kept for older clients. The map reads ``conductance``.
    cohesion: float
    #: ``cut / (2 * intra + cut)`` over production members, lower is tighter.
    #: ``None`` on an older index or when nothing is linked.
    conductance: float | None = None
    #: Members in the requested population; every figure below is over them.
    member_count: int
    hidden_member_count: int = 0
    top_file: str
    avg_pagerank: float
    hotspot_count: int = 0
    dead_count: int = 0
    has_decision: bool = False
    doc_coverage_pct: float = 0.0
    languages: list[str] = []


class ArchitectureEdgeResponse(BaseModel):
    source: int
    target: int
    edge_count: int


class ArchitectureGraphResponse(BaseModel):
    nodes: list[ArchitectureNodeResponse]
    edges: list[ArchitectureEdgeResponse]
    population: PopulationBreakdown | None = None
    unclustered: UnclusteredFiles | None = None


class CommunitySliceNodeResponse(GraphNodeResponse):
    # True for one-hop neighbor stubs outside the community: rendered tiny/dimmed
    # so cross-cluster edges can draw, without pulling the whole neighbor cluster in.
    is_boundary: bool = False


class CommunitySliceResponse(BaseModel):
    # Member nodes of the community plus minimal one-hop boundary stubs.
    nodes: list[CommunitySliceNodeResponse]
    # Edges among members, plus member<->boundary edges (cross-cluster links).
    links: list[GraphEdgeResponse]
    community_id: int
    member_count: int
    # True if members were capped (very large community).
    truncated: bool = False
    hidden_member_count: int = 0


class EgoGraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    links: list[GraphEdgeResponse]
    center_node_id: str
    center_git_meta: GitMetadataResponse | None
    inbound_count: int
    outbound_count: int


class NodeSearchResult(BaseModel):
    node_id: str
    language: str
    symbol_count: int


class DeadCodeGraphNodeResponse(GraphNodeResponse):
    confidence_group: str  # "certain" | "likely" | "neighbor"


class DeadCodeGraphResponse(BaseModel):
    nodes: list[DeadCodeGraphNodeResponse]
    links: list[GraphEdgeResponse]


class HotFilesNodeResponse(GraphNodeResponse):
    commit_count: int


class HotFilesGraphResponse(BaseModel):
    nodes: list[HotFilesNodeResponse]
    links: list[GraphEdgeResponse]


class ModuleNodeResponse(BaseModel):
    module_id: str
    file_count: int
    symbol_count: int
    avg_pagerank: float
    doc_coverage_pct: float
    hotspot_count: int = 0
    dead_count: int = 0
    has_decision: bool = False
    primary_owner: str | None = None


class ModuleEdgeResponse(BaseModel):
    source: str
    target: str
    edge_count: int


class ModuleGraphResponse(BaseModel):
    nodes: list[ModuleNodeResponse]
    edges: list[ModuleEdgeResponse]


class DependencyPathResponse(BaseModel):
    """The shortest dependency path between two nodes.

    ``distance`` is ``-1`` and ``path`` empty when none exists; only then is
    ``visual_context`` present, carrying the nearest common ancestors and
    bridge suggestions the UI falls back to.
    """

    path: list[str] = []
    distance: int
    explanation: str
    visual_context: dict[str, Any] | None = None
