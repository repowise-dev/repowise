"""Execution flow tracing on dependency graphs.

Identifies entry points via composite scoring, then traces call paths
via BFS to discover execution flows. Flows are classified as
intra-community or cross-community based on community assignments.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal, get_args

import networkx as nx
import structlog

from repowise.core.analysis.execution_graph import (
    is_excluded_execution_path,
    is_walkable_execution_edge,
)
from repowise.core.ids import file_path_of

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_ENTRY_POINT_SCORE = 0.3

# Name patterns for entry point scoring (compiled once)
_TIER1_NAMES = re.compile(
    r"^(main|run|start|serve|cli|__main__|app|execute|bootstrap|init)$",
    re.IGNORECASE,
)
_TIER2_NAMES = re.compile(
    r"^(handle_|on_|dispatch_|process_|route_|do_)",
    re.IGNORECASE,
)
_TIER3_NAMES = re.compile(
    r"^(get_|create_|execute_|invoke_|fetch_|submit_|send_|post_)",
    re.IGNORECASE,
)

# Files to exclude from entry point scoring — test, demo, fixture, etc.
# ---------------------------------------------------------------------------
# Why a trace stopped
# ---------------------------------------------------------------------------

# A trace that just ends reads as "execution ends here" whether it does or
# whether the walk ran out of things it could follow. One value per flow, first
# match wins in the order below. Closed; pinned in both directions by
# ``tests/unit/analysis/test_flow_termination_vocabulary.py``.
FlowTermination = Literal[
    # The hop budget ran out; nothing is known about what lay beyond it.
    "depth_limit",
    # The callee rows were cut before the walk saw them, so the three below —
    # which each claim *every* successor — are not available.
    "callees_truncated",
    # Every walkable successor was already on this trace: recursion or a mutual
    # call, not an end to the execution.
    "cycle",
    # Every successor sat below the confidence floor; `termination_detail`
    # carries which origins were declined.
    "confidence_filtered",
    # Every successor was a test/demo/fixture node.
    "excluded_target",
    # No outgoing execution edges were recorded. Deliberately not called a leaf: a
    # symbol whose calls we failed to resolve looks exactly like this, and
    # asserting the code has no callees is the claim we cannot make.
    "no_callees",
]

FLOW_TERMINATION_VALUES: frozenset[str] = frozenset(get_args(FlowTermination))


def classify_termination(
    *,
    hops_taken: int,
    max_depth: int,
    revisited: int,
    low_confidence: int,
    excluded: int,
    truncated: bool = False,
) -> FlowTermination:
    """Name the one thing that stopped a trace.

    Shared by both walks — the in-process one below and the query-time one in
    ``mcp_server/_graph_utils`` — so the two cannot describe one stop with
    different words. Only *truncated* is specific to the query-time walk.
    """
    if hops_taken >= max_depth:
        return "depth_limit"
    if truncated:
        return "callees_truncated"
    if revisited:
        return "cycle"
    if low_confidence:
        return "confidence_filtered"
    if excluded:
        return "excluded_target"
    return "no_callees"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class FlowConfig:
    """Configuration for execution flow tracing."""

    max_depth: int = 8
    max_flows: int = 50
    min_fan_out: int = 2
    deduplicate: bool = True
    # Minimum number of call hops (trace nodes - 1) for a flow to be reported.
    # A single call (depth 1) is not a meaningful "flow"; the onboarding
    # "How It Works" page already gates at >= 3 trace nodes.
    min_flow_depth: int = 2


@dataclass
class ExecutionFlow:
    """A traced execution path from an entry point."""

    entry_point_id: str
    entry_point_name: str
    entry_point_score: float
    trace: list[str]
    depth: int
    crosses_community: bool
    communities_visited: list[int]
    # Required, not defaulted: a flow that cannot say why it stopped is the
    # output this field exists to replace.
    termination: FlowTermination
    # For ``confidence_filtered`` only: {resolution_origin: count}.
    termination_detail: dict[str, int] = field(default_factory=dict)


@dataclass
class ExecutionFlowReport:
    """Summary of all traced execution flows."""

    total_entry_points_scored: int
    total_flows: int
    flows: list[ExecutionFlow]
    # {node_id: score} for *every* candidate scoring >= the entry-point
    # threshold, not just the ones that produced a traced flow. Persisted so
    # the symbol-level "entry point" indicator works for all entry points.
    entry_point_scores: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Entry point scoring
# ---------------------------------------------------------------------------


def _build_call_degree_maps(
    graph: nx.DiGraph,
) -> tuple[dict[str, int], dict[str, int]]:
    """Precompute call-edge in/out degree per node in a single edge pass.

    Replaces repeated per-node ``out_edges``/``in_edges`` rescans (which made
    scoring and successor ordering O(nodes x degree)) with O(1) lookups.
    """
    out_deg: dict[str, int] = defaultdict(int)
    in_deg: dict[str, int] = defaultdict(int)
    for u, v, d in graph.edges(data=True):
        if is_walkable_execution_edge(
            d.get("edge_type"), d.get("resolution_origin"), d.get("confidence")
        ):
            out_deg[u] += 1
            in_deg[v] += 1
    return out_deg, in_deg


def _is_excluded_node(graph: nx.DiGraph, node_id: str) -> bool:
    """True if the node lives in a test/demo/fixture/script path.

    Only symbol nodes carry a ``file_path`` attribute, so the id is the
    fallback. That covers a file node — whose id *is* its path — and an
    unresolved call target, where the bare name is all we have. Both were
    previously read as "no path" and so never excluded, which let
    ``scripts/build.py`` and a mis-resolved ``test_helper`` into traces this
    function exists to keep production-only.
    """
    data = graph.nodes.get(node_id, {})
    file_path = data.get("file_path") or file_path_of(node_id) or ""
    return is_excluded_execution_path(file_path)


def _score_entry_point(
    node_id: str,
    graph: nx.DiGraph,
    community_map: dict[str, int],
    out_deg: dict[str, int],
    in_deg: dict[str, int],
) -> float:
    """Score a symbol as a potential entry point. Returns 0.0-1.0."""
    data = graph.nodes.get(node_id, {})

    # Skip external nodes and test files
    if data.get("node_type") == "external":
        return 0.0
    file_path = data.get("file_path", "") or ""
    if is_excluded_execution_path(file_path):
        return 0.0

    # Signal 1: Fan-out ratio (weight 0.35)
    out_calls = out_deg.get(node_id, 0)
    in_calls = in_deg.get(node_id, 0)
    total = in_calls + out_calls + 1
    fan_out_signal = out_calls / total

    # Signal 2: In-degree threshold (weight 0.25)
    if in_calls == 0:
        in_degree_signal = 1.0
    elif in_calls == 1:
        in_degree_signal = 0.5
    else:
        in_degree_signal = 0.0

    # Signal 3: Visibility (weight 0.20)
    visibility = data.get("visibility", "public")
    if visibility == "public":
        visibility_signal = 1.0
    elif visibility == "protected":
        visibility_signal = 0.3
    else:
        visibility_signal = 0.0

    # Signal 4: Name pattern (weight 0.15)
    name = data.get("name", "")
    if _TIER1_NAMES.match(name):
        name_signal = 1.0
    elif _TIER2_NAMES.match(name):
        name_signal = 0.7
    elif _TIER3_NAMES.match(name):
        name_signal = 0.4
    else:
        name_signal = 0.1

    # Signal 5: Framework entry point hint (weight 0.05)
    # Check if the containing file is marked as an entry point
    file_node_data = graph.nodes.get(file_path, {})
    framework_signal = 1.0 if file_node_data.get("is_entry_point", False) else 0.0

    score = (
        0.35 * fan_out_signal
        + 0.25 * in_degree_signal
        + 0.20 * visibility_signal
        + 0.15 * name_signal
        + 0.05 * framework_signal
    )

    return round(score, 4)


# ---------------------------------------------------------------------------
# BFS tracing
# ---------------------------------------------------------------------------


@dataclass
class _Successors:
    """Walkable call targets, and what was dropped to get there."""

    walkable: list[str]
    low_confidence: Counter  # resolution_origin -> count
    excluded: int


def _get_call_successors(
    node_id: str,
    graph: nx.DiGraph,
    out_deg: dict[str, int],
) -> _Successors:
    """Get outgoing call targets, sorted by out-degree descending.

    Test/demo/fixture nodes are dropped so traces stay on production code
    even when an execution edge mis-resolves to a test fake (e.g. a fake
    ``fetchall`` in a unit test).
    """
    successors = []
    low_confidence: Counter = Counter()
    excluded = 0
    for _, target, d in graph.out_edges(node_id, data=True):
        if not is_walkable_execution_edge(
            d.get("edge_type"), d.get("resolution_origin"), d.get("confidence")
        ):
            low_confidence[d.get("resolution_origin") or "unlabelled"] += 1
            continue
        if _is_excluded_node(graph, target):
            excluded += 1
            continue
        successors.append(target)

    # Sort by out-degree descending to follow primary execution path.
    successors.sort(key=lambda n: out_deg.get(n, 0), reverse=True)
    return _Successors(successors, low_confidence, excluded)


def _bfs_trace(
    entry_id: str,
    graph: nx.DiGraph,
    community_map: dict[str, int],
    config: FlowConfig,
    out_deg: dict[str, int],
) -> ExecutionFlow | None:
    """Trace from an entry point following reliable execution edges.

    Follows the highest-fan-out successor at each step to build
    the primary execution path.
    """
    data = graph.nodes.get(entry_id, {})
    entry_name = data.get("name", entry_id.split("::")[-1] if "::" in entry_id else entry_id)

    visited: set[str] = {entry_id}
    trace: list[str] = [entry_id]
    current = entry_id

    successors = _Successors([], Counter(), 0)
    for _ in range(config.max_depth):
        successors = _get_call_successors(current, graph, out_deg)
        # Pick the first unvisited successor (highest fan-out)
        next_node = None
        for s in successors.walkable:
            if s not in visited:
                next_node = s
                break

        if next_node is None:
            break

        visited.add(next_node)
        trace.append(next_node)
        current = next_node

    # `successors` holds the iteration that found nothing, which is what
    # stopped the walk. When the hop budget ran out instead, that iteration did
    # find one — `classify_termination` reports the budget before reading it.
    termination = classify_termination(
        hops_taken=len(trace) - 1,
        max_depth=config.max_depth,
        revisited=len(successors.walkable),
        low_confidence=sum(successors.low_confidence.values()),
        excluded=successors.excluded,
    )

    # Need at least 2 nodes for a meaningful trace
    if len(trace) < 2:
        return None

    # Determine communities visited
    communities_seen: list[int] = []
    seen_cids: set[int] = set()
    for node in trace:
        cid = community_map.get(node, -1)
        if cid not in seen_cids:
            seen_cids.add(cid)
            communities_seen.append(cid)

    crosses = len(seen_cids) > 1

    return ExecutionFlow(
        entry_point_id=entry_id,
        entry_point_name=entry_name,
        entry_point_score=0.0,  # filled by caller
        trace=trace,
        depth=len(trace) - 1,
        crosses_community=crosses,
        communities_visited=communities_seen,
        termination=termination,
        termination_detail=(
            dict(successors.low_confidence) if termination == "confidence_filtered" else {}
        ),
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _deduplicate_flows(flows: list[ExecutionFlow]) -> list[ExecutionFlow]:
    """Remove flows that share the same first 3 trace nodes, keeping the longest."""
    if not flows:
        return []

    # Group by prefix key (first 3 nodes or full trace if shorter)
    groups: dict[tuple[str, ...], ExecutionFlow] = {}
    for flow in flows:
        key = tuple(flow.trace[:3])
        existing = groups.get(key)
        if existing is None or len(flow.trace) > len(existing.trace):
            groups[key] = flow

    return list(groups.values())


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def trace_execution_flows(
    graph: nx.DiGraph,
    community_map: dict[str, int],
    config: FlowConfig | None = None,
) -> ExecutionFlowReport:
    """Trace execution flows from top-scored entry points.

    Args:
        graph: The full dependency graph with symbol nodes and execution edges.
        community_map: {node_id: community_id} from community detection.
        config: Optional flow tracing configuration.

    Returns:
        ExecutionFlowReport with traced flows sorted by entry point score.
    """
    if config is None:
        config = FlowConfig()

    if graph.number_of_nodes() == 0:
        return ExecutionFlowReport(
            total_entry_points_scored=0,
            total_flows=0,
            flows=[],
        )

    out_deg, in_deg = _build_call_degree_maps(graph)

    # Score all symbol nodes that are functions/methods
    candidates: list[tuple[str, float]] = []
    for node_id, data in graph.nodes(data=True):
        if data.get("node_type") != "symbol":
            continue
        kind = data.get("kind", "")
        if kind not in ("function", "method"):
            continue

        # Must have minimum fan-out to be interesting
        if out_deg.get(node_id, 0) < config.min_fan_out:
            continue

        score = _score_entry_point(node_id, graph, community_map, out_deg, in_deg)
        if score >= _MIN_ENTRY_POINT_SCORE:
            candidates.append((node_id, score))

    candidates.sort(key=lambda x: -x[1])

    # Every scored candidate is a persistable entry point, independent of
    # whether it produced a long enough trace below.
    entry_point_scores = {node_id: score for node_id, score in candidates}

    top_candidates = candidates[: config.max_flows]

    # Trace from each candidate
    flows: list[ExecutionFlow] = []
    for node_id, score in top_candidates:
        flow = _bfs_trace(node_id, graph, community_map, config, out_deg)
        if flow is not None and flow.depth >= config.min_flow_depth:
            flow.entry_point_score = score
            flows.append(flow)

    # Deduplicate
    if config.deduplicate:
        before = len(flows)
        flows = _deduplicate_flows(flows)
        deduped = before - len(flows)
    else:
        deduped = 0

    # Sort by score descending
    flows.sort(key=lambda f: -f.entry_point_score)

    log.info(
        "execution_flows_traced",
        candidates_scored=len(candidates),
        traced=len(flows),
        deduplicated=deduped,
    )

    return ExecutionFlowReport(
        total_entry_points_scored=len(candidates),
        total_flows=len(flows),
        flows=flows,
        entry_point_scores=entry_point_scores,
    )
