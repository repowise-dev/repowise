"""Graph-export response models (file/symbol graph, module graph, ego graph,
dead-code graph, hot-files graph and the community super-node graph)."""

from __future__ import annotations

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
    # Cross-link signals (populated by _collect_node_signals)
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


class GraphExportResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    links: list[GraphEdgeResponse]
    # When the graph is too large to return in full, the server caps the response
    # to top-N nodes by PageRank. Clients should surface a banner.
    truncated: bool = False
    total_node_count: int | None = None


# Architecture / community super-node graph
class ArchitectureNodeResponse(BaseModel):
    community_id: int
    label: str
    cohesion: float
    member_count: int
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
