"""Architecture-view response models (layers, nodes, edges, tour)."""

from __future__ import annotations

from pydantic import BaseModel

from .c4 import C4ExternalSystemResponse


class ArchLayerResponse(BaseModel):
    id: str
    name: str
    description: str
    node_ids: list[str]
    file_count: int
    complexity_distribution: dict[str, int]
    health_score: float | None


class ArchNodeResponse(BaseModel):
    id: str
    node_type: str
    name: str
    file_path: str | None
    line_range: list[int] | None
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


class ArchEdgeResponse(BaseModel):
    source: str
    target: str
    edge_type: str
    direction: str
    weight: float
    confidence: float


class ArchTourStepResponse(BaseModel):
    order: int
    title: str
    description: str
    node_ids: list[str]


class ArchitectureViewResponse(BaseModel):
    project_name: str
    project_description: str
    layers: list[ArchLayerResponse]
    nodes: list[ArchNodeResponse]
    edges: list[ArchEdgeResponse]
    tour: list[ArchTourStepResponse]
    total_files: int
    total_symbols: int
    total_edges: int
    languages: list[str]
    frameworks: list[str]
    external_systems: list[C4ExternalSystemResponse]
