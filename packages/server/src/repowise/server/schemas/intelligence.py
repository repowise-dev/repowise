"""Graph-intelligence response models (callers/callees, communities,
node metrics and execution flows)."""

from __future__ import annotations

from pydantic import BaseModel


class SymbolNodeSummary(BaseModel):
    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int | None = None
    signature: str | None = None


class CallerCalleeEntry(BaseModel):
    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int | None = None
    edge_type: str
    confidence: float


class CallersCalleesResponse(BaseModel):
    symbol_id: str
    symbol: SymbolNodeSummary
    callers: list[CallerCalleeEntry]
    callees: list[CallerCalleeEntry]
    caller_count: int
    callee_count: int
    truncated: bool


class CommunityMember(BaseModel):
    path: str
    pagerank: float
    is_entry_point: bool


class NeighboringCommunity(BaseModel):
    community_id: int
    label: str
    cross_edge_count: int


class CommunityDetailResponse(BaseModel):
    community_id: int
    label: str
    cohesion: float
    member_count: int
    members: list[CommunityMember]
    truncated: bool
    neighboring_communities: list[NeighboringCommunity]


class CommunitySummaryItem(BaseModel):
    community_id: int
    label: str
    cohesion: float
    member_count: int
    top_file: str


class GraphMetricsResponse(BaseModel):
    target: str
    node_type: str
    pagerank: float
    pagerank_percentile: int
    betweenness: float
    betweenness_percentile: int
    community_id: int
    community_label: str | None
    is_entry_point: bool
    in_degree: int
    out_degree: int
    entry_point_score: float | None = None
    kind: str | None = None
    file: str | None = None


class ExecutionFlowEntry(BaseModel):
    entry_point: str
    entry_point_name: str
    entry_point_score: float
    trace: list[str]
    depth: int
    crosses_community: bool
    communities_visited: list[int]


class ExecutionFlowsResponse(BaseModel):
    total_entry_points: int
    flows: list[ExecutionFlowEntry]
