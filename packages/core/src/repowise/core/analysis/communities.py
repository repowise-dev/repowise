"""Community detection on dependency graphs.

Uses Leiden (graspologic) when available, falls back to Louvain (networkx).
Supports both file-level and symbol-level community detection with:
- Oversized community splitting (second pass on communities >25% of graph)
- Cohesion scoring (intra-community edge density)
- Heuristic labeling (dominant directory / keyword analysis)
"""

from __future__ import annotations

import contextlib
import inspect
import io
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_COMMUNITY_FRACTION = 0.30
_MIN_SPLIT_SIZE = 20

# Edge types to include when building file-level community subgraph
_FILE_COMMUNITY_EDGE_TYPES = frozenset({
    "imports", "framework", "dynamic", "extends", "implements",
})

# Edge types to include when building symbol-level community subgraph
_SYMBOL_COMMUNITY_EDGE_TYPES = frozenset({
    "calls", "extends", "implements", "has_method",
})

# Generic directory segments excluded from heuristic labeling.
# These are organisational containers, not meaningful domain labels.
_GENERIC_SEGMENTS = frozenset({
    "src", "lib", "core", "common", "shared", "internal", "pkg",
    "main", "app", "utils", "helpers", "index", "mod",
    # Monorepo organisational directories
    "packages", "modules", "workspace", "workspaces", "libs",
    "projects", "services", "apps",
})

# Keywords checked in filename stems for fallback labeling
_LABEL_KEYWORDS = (
    "api", "auth", "model", "service", "handler", "router", "db",
    "cache", "worker", "util", "test", "config", "middleware", "schema",
    "controller", "view", "store", "hook", "plugin", "adapter",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CommunityInfo:
    """Metadata for a single detected community."""

    community_id: int
    label: str
    members: list[str]
    size: int
    cohesion: float
    dominant_language: str


# ---------------------------------------------------------------------------
# Output suppression (Windows PowerShell 5.1 ANSI fix)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _suppress_graspologic_output():
    """Suppress stdout/stderr during graspologic calls.

    graspologic's leiden() emits ANSI escape sequences that corrupt
    PowerShell 5.1's scroll buffer on Windows.
    """
    old_stderr = sys.stderr
    try:
        sys.stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        sys.stderr = old_stderr


# ---------------------------------------------------------------------------
# Partition (Leiden / Louvain)
# ---------------------------------------------------------------------------


def _partition(G: nx.Graph) -> tuple[dict, str]:
    """Run community detection. Returns ({node: community_id}, algorithm_name).

    Tries Leiden (graspologic) first, falls back to Louvain (networkx).
    """
    try:
        from graspologic.partition import leiden

        with _suppress_graspologic_output():
            result = leiden(G)
        return result, "leiden"
    except ImportError:
        pass

    # Fallback: networkx Louvain
    kwargs: dict = {"seed": 42, "threshold": 1e-4}
    if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
        kwargs["max_level"] = 10

    communities = nx.community.louvain_communities(G, **kwargs)
    assignment = {node: cid for cid, nodes in enumerate(communities) for node in nodes}
    return assignment, "louvain"


# ---------------------------------------------------------------------------
# Oversized community splitting
# ---------------------------------------------------------------------------


def _split_community(
    G: nx.Graph, nodes: list[str],
) -> list[list[str]]:
    """Run a second partition pass on an oversized community subgraph."""
    subgraph = G.subgraph(nodes)
    if subgraph.number_of_edges() == 0:
        return [[n] for n in sorted(nodes)]
    try:
        sub_partition, _ = _partition(subgraph)
        sub_communities: dict[int, list[str]] = {}
        for node, cid in sub_partition.items():
            sub_communities.setdefault(cid, []).append(node)
        if len(sub_communities) <= 1:
            return [sorted(nodes)]
        return [sorted(v) for v in sub_communities.values()]
    except Exception:
        return [sorted(nodes)]


def _split_oversized(
    G: nx.Graph,
    communities: dict[int, list[str]],
    max_fraction: float = _MAX_COMMUNITY_FRACTION,
    min_split_size: int = _MIN_SPLIT_SIZE,
) -> list[list[str]]:
    """Split any community exceeding max_fraction of total nodes."""
    total = sum(len(v) for v in communities.values())
    max_size = max(min_split_size, int(total * max_fraction))

    result: list[list[str]] = []
    for nodes in communities.values():
        if len(nodes) > max_size:
            result.extend(_split_community(G, nodes))
        else:
            result.append(nodes)
    return result


# ---------------------------------------------------------------------------
# Cohesion scoring
# ---------------------------------------------------------------------------


def _cohesion_score(G: nx.Graph, community_nodes: list[str]) -> float:
    """Ratio of actual intra-community edges to maximum possible."""
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    subgraph = G.subgraph(community_nodes)
    actual = subgraph.number_of_edges()
    possible = n * (n - 1) / 2
    return round(actual / possible, 4) if possible > 0 else 0.0


# ---------------------------------------------------------------------------
# Heuristic labeling
# ---------------------------------------------------------------------------


def _collect_path_segments(member_paths: list[str]) -> Counter[str]:
    """Count non-generic directory segments across all member paths.

    Each path contributes at most once per segment to avoid
    double-counting files deep in the same directory tree.
    """
    counter: Counter[str] = Counter()
    for path in member_paths:
        parts = PurePosixPath(path).parts
        seen: set[str] = set()
        for part in parts[:-1]:  # exclude filename
            lower = part.lower()
            if lower not in _GENERIC_SEGMENTS and len(lower) > 1 and not lower.startswith("."):
                if lower not in seen:
                    counter[lower] += 1
                    seen.add(lower)
    return counter


def _best_sub_label(member_paths: list[str], primary: str) -> str:
    """Find the best distinguishing sub-segment within paths that contain *primary*.

    Given a community labeled "web", looks at paths containing "web" and
    picks the most common *other* non-generic segment (e.g. "components",
    "api", "hooks") as a sub-label.
    """
    sub_counter: Counter[str] = Counter()
    primary_lower = primary.lower()

    for path in member_paths:
        parts = PurePosixPath(path).parts
        # Only consider paths that actually contain the primary segment
        lowers = [p.lower() for p in parts[:-1]]
        if primary_lower not in lowers:
            continue
        seen: set[str] = set()
        for part in parts[:-1]:
            lower = part.lower()
            if (
                lower != primary_lower
                and lower not in _GENERIC_SEGMENTS
                and len(lower) > 1
                and not lower.startswith(".")
                and lower not in seen
            ):
                sub_counter[lower] += 1
                seen.add(lower)

    if sub_counter:
        best, best_count = sub_counter.most_common(1)[0]
        # Require at least 30% of members to share this sub-segment
        if best_count / max(len(member_paths), 1) >= 0.3:
            return best
    return ""


def _heuristic_label(member_paths: list[str], community_id: int) -> str:
    """Derive a human-readable label from member file paths.

    Produces labels like ``"web/components"`` or ``"repowise/ingestion"``.
    When multiple communities share the same top-level segment, the
    sub-label differentiates them.
    """
    if not member_paths:
        return f"cluster_{community_id}"

    # Strategy 1: most common non-generic directory segment
    seg_counter = _collect_path_segments(member_paths)

    if seg_counter:
        best_seg, best_count = seg_counter.most_common(1)[0]
        if best_count / len(member_paths) >= 0.4:
            # Try to find a distinguishing sub-segment
            sub = _best_sub_label(member_paths, best_seg)
            return f"{best_seg}/{sub}" if sub else best_seg

    # Strategy 2: keyword frequency in filenames
    stem_counter: Counter[str] = Counter()
    for path in member_paths:
        stem = PurePosixPath(path).stem.lower()
        for kw in _LABEL_KEYWORDS:
            if kw in stem:
                stem_counter[kw] += 1

    if stem_counter:
        best_kw, best_kw_count = stem_counter.most_common(1)[0]
        if best_kw_count / len(member_paths) >= 0.3:
            return best_kw

    # Strategy 3: most common filename stem (excluding generic names)
    stem_counter2: Counter[str] = Counter()
    for path in member_paths:
        stem = PurePosixPath(path).stem.lower()
        if stem not in _GENERIC_SEGMENTS and len(stem) > 1:
            stem_counter2[stem] += 1
    if stem_counter2:
        return stem_counter2.most_common(1)[0][0]

    return f"cluster_{community_id}"


def _deduplicate_labels(communities_info: dict[int, "CommunityInfo"]) -> None:
    """Add sub-labels to disambiguate communities that share the same label.

    Mutates *communities_info* in place.  Only touches communities whose
    label (before the ``/``) duplicates another community.
    """
    # Group by base label (part before "/")
    by_base: dict[str, list[int]] = {}
    for cid, ci in communities_info.items():
        base = ci.label.split("/")[0]
        by_base.setdefault(base, []).append(cid)

    for base, cids in by_base.items():
        if len(cids) <= 1:
            continue

        # Multiple communities share the same base — try to differentiate
        for cid in cids:
            ci = communities_info[cid]
            if "/" in ci.label:
                continue  # already has a sub-label

            sub = _best_sub_label(ci.members, base)
            if sub:
                ci.label = f"{base}/{sub}"

        # If duplicates still remain, append size as disambiguator
        seen_labels: dict[str, int] = {}
        for cid in sorted(cids, key=lambda c: -communities_info[c].size):
            ci = communities_info[cid]
            if ci.label in seen_labels:
                ci.label = f"{ci.label} ({ci.size})"
            seen_labels[ci.label] = cid


def _dominant_language(
    members: list[str], graph: nx.DiGraph,
) -> str:
    """Find the most common language among community members."""
    lang_counter: Counter[str] = Counter()
    for node_id in members:
        data = graph.nodes.get(node_id, {})
        lang = data.get("language")
        if lang and lang != "unknown":
            lang_counter[lang] += 1
    if lang_counter:
        return lang_counter.most_common(1)[0][0]
    return "unknown"


# ---------------------------------------------------------------------------
# Test / production separation
# ---------------------------------------------------------------------------

_TEST_PATH_RE = re.compile(
    r"(test[s_/]|_test\.|\.test\.|\.spec\.|__tests__|conftest|fixture[s]?[/.])",
    re.IGNORECASE,
)


def _is_test_file(path: str) -> bool:
    """True if *path* looks like a test, fixture, or spec file."""
    return bool(_TEST_PATH_RE.search(path))


def _assign_tests_to_communities(
    test_nodes: list[str],
    prod_assignment: dict[str, int],
    graph: nx.DiGraph,
) -> dict[str, int]:
    """Assign each test file to the community of its most-imported production file.

    Falls back to a catch-all "tests" community when no import link exists.
    """
    result: dict[str, int] = {}
    next_cid = max(prod_assignment.values(), default=-1) + 1
    test_community_id = next_cid  # shared fallback

    for test_path in test_nodes:
        # Find which production file this test imports most
        best_prod: str | None = None
        best_weight = 0
        for _, target, d in graph.out_edges(test_path, data=True):
            if target in prod_assignment:
                w = 1
                best_prod = target if w > best_weight else best_prod
                best_weight = max(best_weight, w)
        for source, _, d in graph.in_edges(test_path, data=True):
            if source in prod_assignment:
                w = 1
                best_prod = source if w > best_weight else best_prod
                best_weight = max(best_weight, w)

        if best_prod is not None:
            result[test_path] = prod_assignment[best_prod]
        else:
            result[test_path] = test_community_id

    return result


# ---------------------------------------------------------------------------
# File-level community detection
# ---------------------------------------------------------------------------


def detect_file_communities(
    graph: nx.DiGraph,
) -> tuple[dict[str, int], dict[int, CommunityInfo], str]:
    """Detect communities among file nodes.

    Returns:
        (file_assignment, communities_info, algorithm_used)
        - file_assignment: {file_path: community_id}
        - communities_info: {community_id: CommunityInfo}
        - algorithm_used: "leiden" or "louvain"
    """
    # Extract file nodes (exclude external nodes — they're structural noise)
    file_nodes = [
        n for n, d in graph.nodes(data=True)
        if d.get("node_type", "file") == "file"
    ]

    if not file_nodes:
        return {}, {}, "none"

    # Separate test files from production files.  Test files are clustered
    # separately then assigned to the community of their most-imported
    # production file, preventing test directories from dominating labels
    # and mixing unrelated production modules.
    prod_nodes = [n for n in file_nodes if not _is_test_file(n)]
    test_nodes = [n for n in file_nodes if _is_test_file(n)]

    # Build undirected subgraph from production files + relevant edges
    undirected = nx.Graph()
    undirected.add_nodes_from(prod_nodes)

    for u, v, d in graph.edges(data=True):
        edge_type = d.get("edge_type", "imports")
        if edge_type in _FILE_COMMUNITY_EDGE_TYPES and u in undirected and v in undirected:
            if not undirected.has_edge(u, v):
                undirected.add_edge(u, v)

    # Separate isolates
    isolates = [n for n in undirected.nodes() if undirected.degree(n) == 0]
    connected = [n for n in undirected.nodes() if undirected.degree(n) > 0]

    raw_communities: dict[int, list[str]] = {}
    algorithm = "none"

    if connected:
        connected_subgraph = undirected.subgraph(connected)
        partition, algorithm = _partition(connected_subgraph)

        for node, cid in partition.items():
            raw_communities.setdefault(cid, []).append(node)

    # Each isolate gets its own community
    next_cid = max(raw_communities.keys(), default=-1) + 1
    for node in isolates:
        raw_communities[next_cid] = [node]
        next_cid += 1

    # Split oversized communities
    split_lists = _split_oversized(undirected, raw_communities)

    # Re-index by size descending for deterministic ordering
    split_lists.sort(key=len, reverse=True)

    # Build production file assignment
    prod_assignment: dict[str, int] = {}
    for cid, members in enumerate(split_lists):
        for node in members:
            prod_assignment[node] = cid

    # Assign test files to their most-related production community
    test_assignment = _assign_tests_to_communities(test_nodes, prod_assignment, graph)
    file_assignment = {**prod_assignment, **test_assignment}

    # Build community info (using all members including tests)
    community_members: dict[int, list[str]] = {}
    for node, cid in file_assignment.items():
        community_members.setdefault(cid, []).append(node)

    communities_info: dict[int, CommunityInfo] = {}
    # Also build an undirected graph including test edges for cohesion scoring
    full_undirected = nx.Graph()
    full_undirected.add_nodes_from(file_nodes)
    for u, v, d in graph.edges(data=True):
        edge_type = d.get("edge_type", "imports")
        if edge_type in _FILE_COMMUNITY_EDGE_TYPES and u in full_undirected and v in full_undirected:
            if not full_undirected.has_edge(u, v):
                full_undirected.add_edge(u, v)

    for cid, members in community_members.items():
        sorted_members = sorted(members)
        communities_info[cid] = CommunityInfo(
            community_id=cid,
            label=_heuristic_label(sorted_members, cid),
            members=sorted_members,
            size=len(sorted_members),
            cohesion=_cohesion_score(full_undirected, sorted_members),
            dominant_language=_dominant_language(sorted_members, graph),
        )

    # Deduplicate labels — add sub-labels when multiple communities
    # share the same base (e.g. "web" → "web/components", "web/api")
    _deduplicate_labels(communities_info)

    log.info(
        "file_communities_detected",
        total_files=len(file_nodes),
        prod_files=len(prod_nodes),
        test_files=len(test_nodes),
        communities=len(communities_info),
        algorithm=algorithm,
    )

    return file_assignment, communities_info, algorithm


# ---------------------------------------------------------------------------
# Symbol-level community detection
# ---------------------------------------------------------------------------


def detect_symbol_communities(graph: nx.DiGraph) -> dict[str, int]:
    """Detect communities among symbol nodes using calls/heritage edges.

    Returns {symbol_id: community_id}.
    """
    symbol_nodes = [
        n for n, d in graph.nodes(data=True)
        if d.get("node_type") == "symbol"
    ]

    if not symbol_nodes:
        return {}

    # Build undirected subgraph from call/heritage edges only
    symbol_set = frozenset(symbol_nodes)
    undirected = nx.Graph()
    undirected.add_nodes_from(symbol_nodes)

    for u, v, d in graph.edges(data=True):
        edge_type = d.get("edge_type")
        if (
            edge_type in _SYMBOL_COMMUNITY_EDGE_TYPES
            and u in symbol_set
            and v in symbol_set
        ):
            if not undirected.has_edge(u, v):
                undirected.add_edge(u, v)

    # Separate isolates
    connected = [n for n in undirected.nodes() if undirected.degree(n) > 0]

    if not connected:
        # No edges — each symbol is its own community
        return {sym: i for i, sym in enumerate(sorted(symbol_nodes))}

    connected_subgraph = undirected.subgraph(connected)
    partition, _ = _partition(connected_subgraph)

    # Build communities dict for re-indexing
    raw: dict[int, list[str]] = {}
    for node, cid in partition.items():
        raw.setdefault(cid, []).append(node)

    # Assign isolates
    next_cid = max(raw.keys(), default=-1) + 1
    isolates = [n for n in undirected.nodes() if undirected.degree(n) == 0]
    for node in isolates:
        raw[next_cid] = [node]
        next_cid += 1

    # Re-index by size descending
    ordered = sorted(raw.values(), key=len, reverse=True)
    result: dict[str, int] = {}
    for cid, members in enumerate(ordered):
        for node in members:
            result[node] = cid

    log.info(
        "symbol_communities_detected",
        total_symbols=len(symbol_nodes),
        connected=len(connected),
        communities=len(ordered),
    )

    return result
