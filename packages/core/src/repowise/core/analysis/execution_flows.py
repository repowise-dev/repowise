"""Execution flow tracing on dependency graphs.

Identifies entry points via composite scoring, then traces call paths
via BFS to discover execution flows. Flows are classified as
intra-community or cross-community based on community assignments.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_ENTRY_POINT_SCORE = 0.3

# Name patterns for entry point scoring (compiled once)
_TIER1_NAMES = re.compile(
    r"^(main|run|start|serve|cli|__main__|app|execute|bootstrap|init)$", re.IGNORECASE,
)
_TIER2_NAMES = re.compile(
    r"^(handle_|on_|dispatch_|process_|route_|do_)", re.IGNORECASE,
)
_TIER3_NAMES = re.compile(
    r"^(get_|create_|execute_|invoke_|fetch_|submit_|send_|post_)", re.IGNORECASE,
)

# Files to exclude from entry point scoring — test, demo, fixture, etc.
_EXCLUDE_PATH_PATTERNS = re.compile(
    r"("
    r"test[s_/]|_test\.|\.test\.|\.spec\.|__tests__|conftest|"
    r"fixture[s]?[/.]|mock[s]?[/.]|stub[s]?[/.]|fake[s]?[/.]|"
    r"demo[_/.]|example[s]?[/.]|sample[s]?[/.]|"
    r"benchmark[s]?[/.]|_bench\.|"
    r"scripts?/"
    r")",
    re.IGNORECASE,
)


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


@dataclass
class ExecutionFlowReport:
    """Summary of all traced execution flows."""

    total_entry_points_scored: int
    total_flows: int
    flows: list[ExecutionFlow]


# ---------------------------------------------------------------------------
# Entry point scoring
# ---------------------------------------------------------------------------


def _count_edges_by_type(
    node_id: str, graph: nx.DiGraph, edge_type: str, direction: str,
) -> int:
    """Count edges of a specific type in a given direction."""
    if direction == "out":
        return sum(
            1 for _, _, d in graph.out_edges(node_id, data=True)
            if d.get("edge_type") == edge_type
        )
    return sum(
        1 for _, _, d in graph.in_edges(node_id, data=True)
        if d.get("edge_type") == edge_type
    )


def _score_entry_point(
    node_id: str,
    graph: nx.DiGraph,
    community_map: dict[str, int],
) -> float:
    """Score a symbol as a potential entry point. Returns 0.0-1.0."""
    data = graph.nodes.get(node_id, {})

    # Skip external nodes and test files
    if data.get("node_type") == "external":
        return 0.0
    file_path = data.get("file_path", "") or ""
    if _EXCLUDE_PATH_PATTERNS.search(file_path):
        return 0.0

    # Signal 1: Fan-out ratio (weight 0.35)
    out_calls = _count_edges_by_type(node_id, graph, "calls", "out")
    in_calls = _count_edges_by_type(node_id, graph, "calls", "in")
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


def _get_call_successors(
    node_id: str, graph: nx.DiGraph,
) -> list[str]:
    """Get outgoing call targets, sorted by out-degree descending."""
    successors = []
    for _, target, d in graph.out_edges(node_id, data=True):
        if d.get("edge_type") == "calls" and d.get("confidence", 0) >= 0.5:
            successors.append(target)

    # Sort by out-degree descending to follow primary execution path
    successors.sort(
        key=lambda n: sum(
            1 for _, _, d in graph.out_edges(n, data=True)
            if d.get("edge_type") == "calls"
        ),
        reverse=True,
    )
    return successors


def _bfs_trace(
    entry_id: str,
    graph: nx.DiGraph,
    community_map: dict[str, int],
    config: FlowConfig,
) -> ExecutionFlow | None:
    """BFS trace from an entry point following call edges.

    Follows the highest-fan-out successor at each step to build
    the primary execution path.
    """
    data = graph.nodes.get(entry_id, {})
    entry_name = data.get("name", entry_id.split("::")[-1] if "::" in entry_id else entry_id)

    visited: set[str] = {entry_id}
    trace: list[str] = [entry_id]
    current = entry_id

    for _ in range(config.max_depth):
        successors = _get_call_successors(current, graph)
        # Pick the first unvisited successor (highest fan-out)
        next_node = None
        for s in successors:
            if s not in visited:
                next_node = s
                break

        if next_node is None:
            break

        visited.add(next_node)
        trace.append(next_node)
        current = next_node

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
        graph: The full dependency graph with symbol nodes and call edges.
        community_map: {node_id: community_id} from community detection.
        config: Optional flow tracing configuration.

    Returns:
        ExecutionFlowReport with traced flows sorted by entry point score.
    """
    if config is None:
        config = FlowConfig()

    if graph.number_of_nodes() == 0:
        return ExecutionFlowReport(
            total_entry_points_scored=0, total_flows=0, flows=[],
        )

    # Score all symbol nodes that are functions/methods
    candidates: list[tuple[str, float]] = []
    for node_id, data in graph.nodes(data=True):
        if data.get("node_type") != "symbol":
            continue
        kind = data.get("kind", "")
        if kind not in ("function", "method"):
            continue

        # Must have minimum fan-out to be interesting
        out_calls = sum(
            1 for _, _, d in graph.out_edges(node_id, data=True)
            if d.get("edge_type") == "calls"
        )
        if out_calls < config.min_fan_out:
            continue

        score = _score_entry_point(node_id, graph, community_map)
        if score >= _MIN_ENTRY_POINT_SCORE:
            candidates.append((node_id, score))

    candidates.sort(key=lambda x: -x[1])
    top_candidates = candidates[:config.max_flows]

    # Trace from each candidate
    flows: list[ExecutionFlow] = []
    for node_id, score in top_candidates:
        flow = _bfs_trace(node_id, graph, community_map, config)
        if flow is not None:
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
    )
