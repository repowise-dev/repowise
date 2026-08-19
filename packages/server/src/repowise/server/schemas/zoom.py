"""Zoom-map response models.

One nested containment tree (system -> layer -> group -> folder -> file) served
as a flat list of nodes (each carrying its own id), plus parent-relative
relations. The canvas renderer indexes the list by id and reconstructs the tree
from ``parent_id`` / ``children``, then uses ``layout`` (parent ``[0,1]`` space)
for clip-and-scale and ``importance`` / ``sibling_rank`` for density caps. The
list is emitted in a stable order (sorted by id).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ZoomRectResponse(BaseModel):
    x: float
    y: float
    w: float
    h: float


class ZoomMetricsResponse(BaseModel):
    file_count: int = 0
    descendant_count: int = 0
    hotspot_count: int = 0
    dead_count: int = 0
    entry_point_count: int = 0
    on_flow_count: int = 0


class ZoomNodeResponse(BaseModel):
    id: str
    parent_id: str | None = None
    level: int
    kind: str  # system | layer | group | folder | file
    name: str
    path: str = ""
    children: list[str] = Field(default_factory=list)
    importance: float = 0.0
    sibling_rank: int = 0
    metrics: ZoomMetricsResponse = Field(default_factory=ZoomMetricsResponse)
    layout: ZoomRectResponse | None = None
    summary: str = ""
    language: str | None = None
    # Code-health score (0..10, higher = healthier), matching the /files treemap.
    # None when the file/subtree was unscored (health is sparse); the renderer
    # reads that as neutral.
    health_score: float | None = None
    is_entry_point: bool = False
    is_hotspot: bool = False
    is_dead: bool = False
    is_test: bool = False
    on_flow: bool = False


class ZoomRelationResponse(BaseModel):
    parent_id: str
    source_id: str
    target_id: str
    label: str = ""
    edge_count: int = 1
    coupling: str = ""  # loose | moderate | tight


class ZoomMapResponse(BaseModel):
    root_id: str
    project_name: str
    total_files: int
    max_depth: int
    truncated: bool = False
    nodes: list[ZoomNodeResponse]
    relations: list[ZoomRelationResponse]
