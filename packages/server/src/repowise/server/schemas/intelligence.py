"""Graph-intelligence response models (callers/callees, communities,
node metrics and execution flows)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from repowise.core.persistence.models import GraphEdge, GraphNode


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
    # None on an index built before origins were stamped.
    resolution_origin: str | None = None

    @classmethod
    def from_edge(
        cls,
        edge: GraphEdge,
        *,
        node_id: str,
        node_map: Mapping[str, Any],
    ) -> CallerCalleeEntry:
        """Build the row for the endpoint *opposite* ``node_id`` on ``edge``.

        The one place a *response-model* row is built from an edge. It used to
        be two: `symbol_detail` and `get_callers_callees` each built the same
        eight fields, and they drifted — the null guards below were added to
        the first and had to be re-added to the second after a null `name`
        failed response validation for a whole call instead of degrading one
        row. All three columns are nullable while the response fields are not,
        so each needs the *value* checked, not just the row.

        `mcp_server/tool_context/enrichment.py` builds a third, deliberately
        different shape for the agent surface (definition line, a confidence
        floor, `via` rather than `resolution_origin`). It is not a copy of
        this and is not folded in here.
        """
        other_id = edge.source_node_id if edge.target_node_id == node_id else edge.target_node_id
        other: GraphNode | None = node_map.get(other_id)
        return cls(
            symbol_id=other_id,
            name=other.name
            if other and other.name
            else (other_id.split("::")[-1] if "::" in other_id else other_id),
            kind=other.kind if other and other.kind else "unknown",
            file=other.file_path
            if other and other.file_path
            else (other_id.split("::")[0] if "::" in other_id else other_id),
            start_line=other.start_line if other else None,
            edge_type=edge.edge_type,
            confidence=round(edge.confidence or 0.0, 3),
            resolution_origin=edge.resolution_origin,
        )


# ---------------------------------------------------------------------------
# Symbol relation vocabulary
# ---------------------------------------------------------------------------

# `SYMBOL_USE_EDGE_TYPES` answers "does something reach this symbol", which is
# the right question for reachability and the wrong one for a reader: a
# subclass reaches its base without ever calling it. Partitioning by what the
# relation *is* lets each kind be counted and named separately, so a symbol
# with 1,516 subclasses and 8 callers no longer serves 39 subclasses and one
# caller under a single "Called by" heading.
#
# `SYMBOL_RELATION_GROUPS` must partition `SYMBOL_USE_EDGE_TYPES` exactly;
# `test_symbol_relations.py` fails if an edge type is added to one and not the
# other, so a new relation kind cannot land unnamed.
SYMBOL_RELATION_GROUPS: dict[str, frozenset[str]] = {
    "call": frozenset({"calls"}),
    "heritage": frozenset({"extends", "implements", "method_implements", "dispatches_to"}),
    "wiring": frozenset({"framework_binds"}),
    "reference": frozenset({"reads", "references"}),
}

#: Reverse index, so a fetched edge can name its own group without a scan.
SYMBOL_RELATION_GROUP_OF: dict[str, str] = {
    edge_type: group
    for group, edge_types in SYMBOL_RELATION_GROUPS.items()
    for edge_type in edge_types
}


class SymbolRelationGroup(BaseModel):
    """One relation kind, on one side of a symbol, with its true total.

    `total` is counted unbounded while `rows` is capped, so a surface can say
    "12 of 1,516" instead of silently rendering the cap as the count.
    """

    direction: Literal["in", "out"]
    edge_type: str
    group: str
    total: int
    rows: list[CallerCalleeEntry]


class CallersCalleesResponse(BaseModel):
    symbol_id: str
    symbol: SymbolNodeSummary
    #: `calls` edges only. Every other relation kind is in `relations`, so a
    #: subclass is never served as a caller.
    callers: list[CallerCalleeEntry]
    callees: list[CallerCalleeEntry]
    #: True totals, not the number of rows served. `symbol-drawer-wrapper.tsx`
    #: renders these as `caller_total`, and they used to be `len(callers)`
    #: capped at the request limit.
    caller_count: int
    callee_count: int
    truncated: bool
    #: Defaulted so a client reading an older server sees an absent group list
    #: rather than a validation error.
    relations: list[SymbolRelationGroup] = []


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
    # False when this node appeared after the last exact centrality scoring, so
    # the two fields above are the column default rather than a measurement.
    # Additive and defaulted so existing clients keep their shape.
    betweenness_scored: bool = True
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
    # Why the walk stopped, and how each hop was resolved. Both None/absent on
    # an index predating them. `trace_via` is pairwise with `trace`, so it is
    # one shorter: `trace_via[i]` describes the hop out of `trace[i]`.
    termination: str | None = None
    termination_detail: dict[str, int] | None = None
    trace_via: list[str | None] | None = None


class ExecutionFlowsResponse(BaseModel):
    total_entry_points: int
    flows: list[ExecutionFlowEntry]
