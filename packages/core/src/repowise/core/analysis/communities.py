"""Community detection on dependency graphs.

Uses Leiden (graspologic's native core) when available, falls back to Louvain (networkx).
Supports both file-level and symbol-level community detection with:
- Oversized community splitting (second pass on communities >25% of graph)
- Cohesion scoring (intra-community edge density)
- Heuristic labeling (dominant directory / keyword analysis)
"""

from __future__ import annotations

import functools
import hashlib
import inspect
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx
import structlog

from repowise.core.analysis._leiden import leiden_partition
from repowise.core.analysis.kg_curation import GENERIC_ORG_SEGMENTS, dominant_segments
from repowise.core.ids import is_external
from repowise.core.ingestion.models import (
    FILE_DEPENDENCY_EDGE_TYPES,
    SYMBOL_USE_EDGE_TYPES,
    TEMPORAL_EDGE_TYPES,
)
from repowise.core.support_paths import is_example_path
from repowise.core.test_paths import is_test_related_path

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_COMMUNITY_FRACTION = 0.30
_MIN_SPLIT_SIZE = 20

# Stored community id for a node the partition leaves out. Persisted rows carry
# a NOT NULL id defaulting to 0, which is the largest real community, so an
# external node must not fall through to it.
NO_COMMUNITY = -1


def stored_community_id(assignment: dict[str, int], node_id: str) -> int:
    """Community id to persist for *node_id*: its own, else 0, or ``NO_COMMUNITY`` for external code."""
    if node_id in assignment:
        return assignment[node_id]
    return NO_COMMUNITY if is_external(node_id) else 0


def has_community(community_id: int | None) -> bool:
    """True when *community_id* names a real community.

    ``None`` (never assigned) and ``NO_COMMUNITY`` (external code, left out of
    the partition) both mean no community, so readers must not treat them as a
    cluster.
    """
    return community_id is not None and community_id >= 0


# Edge types to include when building file-level community subgraph.
# Was {imports, framework, dynamic, extends, implements}: "dynamic" matched no
# real edge, and extends/implements are symbol-to-symbol so they never joined
# two files.
_FILE_COMMUNITY_EDGE_TYPES = FILE_DEPENDENCY_EDGE_TYPES

# Edge types to include when building symbol-level community subgraph.
# Containment is kept here on purpose: a class and its methods belong in one
# community.
_SYMBOL_COMMUNITY_EDGE_TYPES = SYMBOL_USE_EDGE_TYPES | {"has_method"}

# Generic directory segments excluded from heuristic labeling — the shared
# organisational-container vocabulary lives in kg_curation next to its
# data-driven complement (dominant_segments).
_GENERIC_SEGMENTS = GENERIC_ORG_SEGMENTS

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
    #: ``cut / (2 * intra + cut)`` over the production members, lower is
    #: tighter. ``None`` when nothing is linked. Does not decay with size.
    conductance: float | None = None


# ---------------------------------------------------------------------------
# Partition (Leiden / Louvain)
# ---------------------------------------------------------------------------


def _directory_fallback(nodes: list[str]) -> dict[str, int]:
    """Group nodes by first-level directory when graph-based community detection fails."""
    dir_to_id: dict[str, int] = {}
    result: dict[str, int] = {}
    next_id = 1
    for path in nodes:
        parts = path.split("/")
        dir_key = parts[0] if len(parts) > 1 else "__root__"
        if dir_key not in dir_to_id:
            dir_to_id[dir_key] = next_id
            next_id += 1
        result[path] = dir_to_id[dir_key]
    return result


def _canonical(graph: nx.Graph) -> nx.Graph:
    """*graph* rebuilt with nodes, and each edge's endpoints, in sorted order."""
    ordered = nx.Graph()
    ordered.add_nodes_from(sorted(graph.nodes()))
    ordered.add_edges_from(
        sorted(
            ((min(u, v), max(u, v), data) for u, v, data in graph.edges(data=True)),
            key=lambda edge: edge[:2],
        )
    )
    return ordered


def _partition(graph: nx.Graph) -> tuple[dict, str]:
    """Run community detection. Returns ({node: community_id}, algorithm_name).

    Tries Leiden (graspologic_native) first, falls back to Louvain (networkx),
    then to directory-based grouping as a last resort.

    Both algorithms depend on node and edge order even when seeded, so the
    graph is rebuilt in sorted order first. Callers pass ``subgraph`` views,
    and a view that keeps under half its parent's nodes iterates them in set
    order, which changes with the process's hash seed: the same graph gave a
    different partition on every run.
    """
    graph = _canonical(graph)
    try:
        result = leiden_partition(graph, seed=42)  # determinism (matches louvain's seed)
        return result, "leiden"
    except ImportError:
        pass

    # Fallback: networkx Louvain
    try:
        kwargs: dict = {"seed": 42, "threshold": 1e-4}
        if "max_level" in inspect.signature(nx.community.louvain_communities).parameters:
            kwargs["max_level"] = 10

        communities = nx.community.louvain_communities(graph, **kwargs)
        assignment = {node: cid for cid, nodes in enumerate(communities) for node in nodes}
        return assignment, "louvain"
    except Exception as exc:
        log.warning("louvain_failed_using_directory_fallback", error=str(exc))

    # Final fallback: directory-based grouping
    # Sorted: _directory_fallback numbers directories in first-seen order.
    return _directory_fallback(sorted(graph.nodes())), "directory"


# ---------------------------------------------------------------------------
# Oversized community splitting
# ---------------------------------------------------------------------------


def _split_community(
    graph: nx.Graph, nodes: list[str],
) -> list[list[str]]:
    """Run a second partition pass on an oversized community subgraph."""
    subgraph = graph.subgraph(nodes)
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
    graph: nx.Graph,
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
            result.extend(_split_community(graph, nodes))
        else:
            result.append(nodes)
    return result


# ---------------------------------------------------------------------------
# Cohesion scoring
# ---------------------------------------------------------------------------


def _cohesion_score(graph: nx.Graph, community_nodes: list[str]) -> float:
    """Ratio of actual intra-community edges to maximum possible."""
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    subgraph = graph.subgraph(community_nodes)
    actual = subgraph.number_of_edges()
    possible = n * (n - 1) / 2
    return round(actual / possible, 4) if possible > 0 else 0.0


def _conductance(graph: nx.Graph, community_nodes: list[str]) -> float | None:
    """Share of the community's edge volume that crosses its boundary.

    ``cohesion`` divides by ``n(n-1)/2`` and so measures size on anything
    above a few dozen files; this compares edges leaving against edges staying.
    """
    members = set(community_nodes)
    intra = 0
    cut = 0
    for node in members:
        if node not in graph:
            continue
        for neighbor in graph.neighbors(node):
            if neighbor in members:
                intra += 1  # counted from both ends: 2 * intra
            else:
                cut += 1
    volume = intra + cut  # intra is already doubled
    if volume == 0:
        return None
    return round(cut / volume, 4)


# ---------------------------------------------------------------------------
# Heuristic labeling
# ---------------------------------------------------------------------------


def _collect_path_segments(
    member_paths: list[str], extra_generic: frozenset[str] = frozenset()
) -> Counter[str]:
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
            if (
                lower not in _GENERIC_SEGMENTS
                and lower not in extra_generic
                and len(lower) > 1
                and not lower.startswith(".")
                and lower not in seen
            ):
                counter[lower] += 1
                seen.add(lower)
    return counter


def _best_sub_label(
    member_paths: list[str], primary: str, extra_generic: frozenset[str] = frozenset()
) -> str:
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
                and lower not in extra_generic
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


def _join_segments(member_paths: list[str], primary: str, sub: str) -> str:
    """``"a/b"`` with the two segments in the order the paths nest them.

    Frequency picked them, so it says nothing about which is the parent.
    Ties keep *primary* first.
    """
    primary_first = 0
    sub_first = 0
    for path in member_paths:
        lowers = [p.lower() for p in PurePosixPath(path).parts[:-1]]
        if primary in lowers and sub in lowers:
            if lowers.index(primary) <= lowers.index(sub):
                primary_first += 1
            else:
                sub_first += 1
    return f"{sub}/{primary}" if sub_first > primary_first else f"{primary}/{sub}"


def _heuristic_label(
    member_paths: list[str],
    community_id: int,
    extra_generic: frozenset[str] = frozenset(),
) -> str:
    """Derive a human-readable label from member file paths.

    Produces labels like ``"web/components"`` or ``"core/ingestion"``.
    When multiple communities share the same top-level segment, the
    sub-label differentiates them. *extra_generic* carries repo-dominant
    namespace segments (the repo's own name, ``src``…) that must never
    become labels.
    """
    if not member_paths:
        return f"cluster_{community_id}"

    # Strategy 1: most common non-generic directory segment
    seg_counter = _collect_path_segments(member_paths, extra_generic)

    if seg_counter:
        best_seg, best_count = seg_counter.most_common(1)[0]
        if best_count / len(member_paths) >= 0.4:
            # Try to find a distinguishing sub-segment
            sub = _best_sub_label(member_paths, best_seg, extra_generic)
            return _join_segments(member_paths, best_seg, sub) if sub else best_seg
        # Two areas sharing one community: name both rather than fall through
        # to a filename stem.
        top = seg_counter.most_common(2)
        if len(top) == 2 and all(c / len(member_paths) >= 0.2 for _, c in top):
            return f"{top[0][0]}, {top[1][0]}"

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
        if (
            stem not in _GENERIC_SEGMENTS
            and stem not in extra_generic
            and len(stem) > 1
            and not stem.startswith("__")  # __init__, __main__: not a name
        ):
            stem_counter2[stem] += 1
    if stem_counter2:
        return stem_counter2.most_common(1)[0][0]

    return f"cluster_{community_id}"


def _deduplicate_labels(
    communities_info: dict[int, CommunityInfo],
    extra_generic: frozenset[str] = frozenset(),
    label_members: dict[int, list[str]] | None = None,
) -> None:
    """Add sub-labels to disambiguate communities that share the same label.

    Mutates *communities_info* in place.  Only touches communities whose
    label (before the ``/``) duplicates another community. *label_members*
    narrows the paths a sub-label is drawn from (the production members);
    without it every member counts.
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

            paths = label_members.get(cid, ci.members) if label_members else ci.members
            sub = _best_sub_label(paths, base, extra_generic)
            if sub:
                ci.label = _join_segments(paths, base, sub)

        # If duplicates still remain, append the labelled member count
        def _count(c: int) -> int:
            return len(label_members[c]) if label_members and c in label_members else communities_info[c].size

        seen_labels: dict[str, int] = {}
        for cid in sorted(cids, key=lambda c: -_count(c)):
            ci = communities_info[cid]
            if ci.label in seen_labels:
                ci.label = f"{ci.label} ({_count(cid)})"
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

def _is_test_node(node_id: str, data: dict) -> bool:
    """Whether a graph file node is test material.

    Reads the flag ingestion stored on the node, which was decided with the
    file's language in hand. The path rules are the fallback for a node that
    predates the flag. The regex this replaced matched ``test[s_/]`` unanchored,
    so ``src/latest/api.py`` and ``protest/main.py`` were pulled out of
    community assignment as tests (#1103).
    """
    if "is_test" in data:
        return bool(data["is_test"])
    return is_test_related_path(node_id, data.get("language"))


def _is_example_node(node_id: str, data: dict) -> bool:
    """Whether a graph file node is example, demo, or benchmark code.

    Derived from the path rather than a stored flag. Unlike ``is_test`` there
    is no ``is_example`` on the node, and the path is the whole signal, so this
    does not justify a new ``FileInfo`` field and ``GraphNode`` column.
    """
    return is_example_path(node_id)


def _assign_tests_to_communities(
    test_nodes: list[str],
    prod_assignment: dict[str, int],
    graph: nx.DiGraph,
) -> dict[str, int]:
    """Assign each non-core file to the community of a file it links to.

    Falls back to a shared catch-all community when no import link exists.
    Used for tests and for example/benchmark code alike: both are real files a
    reader may open, but neither should shape what the production communities
    are or what they are called.
    """
    result: dict[str, int] = {}
    next_cid = max(prod_assignment.values(), default=-1) + 1
    test_community_id = next_cid  # shared fallback

    for test_path in test_nodes:
        # The community most of the linked production files belong to, one
        # vote per linked file, either direction. Ties break on community id:
        # edge iteration order is insertion order and varies between runs.
        # Co-change is history, not a link, and init adds it after this runs.
        votes: Counter[int] = Counter()
        linked = {
            t
            for _, t, kind in graph.out_edges(test_path, data="edge_type")
            if t in prod_assignment and kind not in TEMPORAL_EDGE_TYPES
        }
        linked |= {
            s
            for s, _, kind in graph.in_edges(test_path, data="edge_type")
            if s in prod_assignment and kind not in TEMPORAL_EDGE_TYPES
        }
        for prod_path in linked:
            votes[prod_assignment[prod_path]] += 1

        if votes:
            result[test_path] = min(votes, key=lambda cid: (-votes[cid], cid))
        else:
            result[test_path] = test_community_id

    return result


# ---------------------------------------------------------------------------
# File-level community detection
# ---------------------------------------------------------------------------


@functools.cache
def community_code_fingerprint() -> str:
    """Hash of the code and libraries a partition depends on besides its graph.

    A stored partition is only reused under the same fingerprint, so an upgrade
    that changes the algorithm, or the backend it falls to, detects afresh.
    """
    from importlib.metadata import PackageNotFoundError, version

    from repowise.core.analysis import _leiden, kg_curation

    digest = hashlib.sha256()
    # kg_curation holds the label vocabulary the community info is named with.
    for module_file in (__file__, _leiden.__file__, kg_curation.__file__):
        digest.update(Path(module_file).read_bytes())
    digest.update(nx.__version__.encode())
    try:
        digest.update(version("graspologic-native").encode())
    except PackageNotFoundError:
        digest.update(b"no leiden")
    return digest.hexdigest()


@dataclass(frozen=True)
class FileCommunityInput:
    """What :func:`detect_file_communities` reads from a graph, hashed and fingerprinted.

    *signature* changes with any of it, so equal signatures mean an equal
    result. *nodes* and *edges* fingerprint it item by item (each file node
    with its test/example flag and language; each non-temporal edge between
    two file nodes with its type), so a cache can count how far it drifted.
    """

    signature: str
    nodes: frozenset[int]
    edges: frozenset[int]


#: :func:`split_file_nodes`' result: sorted file nodes, and whether each is test or example code.
FileNodes = tuple[list[str], dict[str, bool]]


def split_file_nodes(graph: nx.DiGraph) -> FileNodes:
    """*graph*'s own file nodes, sorted, and whether each is test or example code.

    External and framework nodes are stored as files but are code we do not
    own: left in, they join the partition (two files that both import `os` get
    pulled together through it) and inflate members/size. Sorted: node order
    seeds the undirected graph's insertion order, and Louvain/Leiden partitions
    depend on iteration order even when seeded.
    """
    file_nodes = sorted(
        n for n, d in graph.nodes(data=True)
        if d.get("node_type", "file") == "file" and not is_external(n)
    )
    non_core = {
        n: _is_test_node(n, graph.nodes[n]) or _is_example_node(n, graph.nodes[n])
        for n in file_nodes
    }
    return file_nodes, non_core


def _production_edges(graph: nx.DiGraph, production: list[str]) -> list[tuple[str, str]]:
    """The links the partition runs on: dependency edges between two of *production*.

    Read from *production*'s own out-edges, not the whole graph's, which are
    mostly symbol edges. Sorted: edge insertion order shifts the partition
    (co-change edges arrive in git-indexer thread-completion order).
    """
    members = frozenset(production)
    return sorted(
        (u, v)
        for u, v, d in graph.edges(production, data=True)
        if d.get("edge_type", "imports") in _FILE_COMMUNITY_EDGE_TYPES
        and v in members
    )


def file_community_input(
    graph: nx.DiGraph, repo_name: str | None = None, files: FileNodes | None = None
) -> FileCommunityInput:
    """:class:`FileCommunityInput` of *graph*; *files* is its :func:`split_file_nodes`."""
    from repowise.core.ingestion.graph._centrality_cache import fingerprints

    file_nodes, non_core = files or split_file_nodes(graph)
    file_set = frozenset(file_nodes)
    node_items = [f"{n}\0{non_core[n]}\0{graph.nodes[n].get('language')}" for n in file_nodes]
    edge_items = sorted(
        f"{u}\0{v}\0{d.get('edge_type', '<absent>')}"
        for u, v, d in graph.edges(file_nodes, data=True)
        if v in file_set and d.get("edge_type") not in TEMPORAL_EDGE_TYPES
    )
    digest = hashlib.sha256(f"{repo_name}\0{community_code_fingerprint()}".encode())
    for item in (*node_items, "\1", *edge_items):
        digest.update(item.encode("utf-8", "replace"))
        digest.update(b"\n")
    return FileCommunityInput(
        digest.hexdigest(), fingerprints(node_items), fingerprints(edge_items)
    )


def production_community_input(
    graph: nx.DiGraph, files: FileNodes | None = None
) -> FileCommunityInput:
    """What the production partition reads: the production files and the links between them.

    Test and example files are placed after the partition and never shape it,
    so adding, removing or relinking one leaves this unchanged.
    """
    from repowise.core.ingestion.graph._centrality_cache import fingerprints

    file_nodes, non_core = files or split_file_nodes(graph)
    production = [n for n in file_nodes if not non_core[n]]
    edge_items = [f"{u}\0{v}" for u, v in _production_edges(graph, production)]
    digest = hashlib.sha256(community_code_fingerprint().encode())
    for item in (*production, "\1", *edge_items):
        digest.update(item.encode("utf-8", "replace"))
        digest.update(b"\n")
    return FileCommunityInput(
        digest.hexdigest(), fingerprints(production), fingerprints(edge_items)
    )


@dataclass(frozen=True)
class ProductionPartition:
    """The half of a file detection that test and example files never change.

    *assignment* is each production file's community and *algorithm* the
    partitioner that made it. *labels* names each production community before
    duplicate names are told apart; it is drawn from production members only,
    with *generic* as the segments no label may use.
    """

    assignment: dict[str, int]
    labels: dict[int, str]
    generic: frozenset[str]
    algorithm: str


def detect_file_communities(
    graph: nx.DiGraph,
    repo_name: str | None = None,
) -> tuple[dict[str, int], dict[int, CommunityInfo], str]:
    """Detect communities among file nodes.

    *repo_name* is a generic segment for labelling: a monorepo repeating
    ``packages/<x>/src/<repo>`` keeps it under ``dominant_segments``' bar.

    Returns:
        (file_assignment, communities_info, algorithm_used)
        - file_assignment: {file_path: community_id}
        - communities_info: {community_id: CommunityInfo}
        - algorithm_used: "leiden" or "louvain"
    """
    return place_file_communities(graph, repo_name)[:3]


def place_file_communities(
    graph: nx.DiGraph,
    repo_name: str | None = None,
    production: ProductionPartition | None = None,
    files: FileNodes | None = None,
) -> tuple[dict[str, int], dict[int, CommunityInfo], str, ProductionPartition]:
    """:func:`detect_file_communities` on top of a *production* partition, when given.

    Test and example files are placed and every community's info is built as
    detection does; only the production partition, and the labels it names,
    are taken from *production*. It must cover exactly *graph*'s production
    files, else it is set aside and detected afresh. Also returns the
    production partition the result stands on: *production* itself when it
    was used, else the one detected here. *files* is *graph*'s
    :func:`split_file_nodes`, when the caller already has it.
    """
    file_nodes, non_core = files or split_file_nodes(graph)
    if not file_nodes:
        return {}, {}, "none", ProductionPartition({}, {}, frozenset(), "none")

    # Separate test and example files from production files. Both are clustered
    # separately then assigned to the community of a production file they link
    # to, preventing those directories from dominating labels and mixing
    # unrelated production modules.
    #
    # Examples join tests here rather than being dropped: demos import across
    # the whole system, so left in the partition they merge otherwise-distinct
    # subsystems into one community and name it after the demo. They keep their
    # nodes and their assignment, so nothing becomes unreachable.
    prod_nodes = [n for n in file_nodes if not non_core[n]]
    test_nodes = [n for n in file_nodes if non_core[n]]
    if production is not None and production.assignment.keys() != set(prod_nodes):
        production = None

    # Build undirected subgraph from production files + relevant edges
    undirected = nx.Graph()
    undirected.add_nodes_from(prod_nodes)
    undirected.add_edges_from(_production_edges(graph, prod_nodes))

    if production is None:
        prod_assignment, algorithm = _partition_production(undirected)
    else:
        prod_assignment, algorithm = production.assignment, production.algorithm

    # Assign test files to their most-related production community
    test_assignment = _assign_tests_to_communities(test_nodes, prod_assignment, graph)
    file_assignment = {**prod_assignment, **test_assignment}

    extra_generic = _label_generic(file_nodes, repo_name)
    # A production community's label reads only its production members, so a
    # held one stands while the segments it was drawn without are the same.
    known_labels = (
        production.labels if production is not None and production.generic == extra_generic else {}
    )
    communities_info, production_labels = _community_info(
        graph, file_assignment, non_core, undirected, extra_generic, known_labels
    )

    log.info(
        "file_communities_detected",
        total_files=len(file_nodes),
        prod_files=len(prod_nodes),
        test_files=len(test_nodes),
        communities=len(communities_info),
        algorithm=algorithm,
        production_reused=production is not None,
    )

    if production is None:
        production = ProductionPartition(
            prod_assignment, production_labels, extra_generic, algorithm
        )
    return file_assignment, communities_info, algorithm, production


def _label_generic(file_nodes: list[str], repo_name: str | None) -> frozenset[str]:
    """Segments no community label may use."""
    # Repo-dominant namespace segments (the repo's own package name, ``src``…)
    # are noise, not labels — same data-driven stripping as module naming.
    generic = {s.lower() for s in dominant_segments(sorted(file_nodes))}
    if repo_name:
        stem = repo_name.rsplit("/", 1)[-1].lower()
        generic |= {stem, stem.replace("-", "_"), stem.replace("_", "-")}
    return frozenset(generic)


def _community_info(
    graph: nx.DiGraph,
    file_assignment: dict[str, int],
    non_core: dict[str, bool],
    undirected: nx.Graph,
    extra_generic: frozenset[str],
    known_labels: dict[int, str],
) -> tuple[dict[int, CommunityInfo], dict[int, str]]:
    """Each community's info, and each production community's label before deduplication.

    *undirected* is the production link graph; *known_labels* are production
    labels already drawn with *extra_generic*, used instead of drawing again.
    """
    file_nodes = list(non_core)  # keyed in sorted file-node order
    # Build community info (using all members including tests)
    community_members: dict[int, list[str]] = {}
    for node, cid in file_assignment.items():
        community_members.setdefault(cid, []).append(node)

    communities_info: dict[int, CommunityInfo] = {}
    # Also build an undirected graph including test edges for cohesion scoring
    full_undirected = nx.Graph()
    full_undirected.add_nodes_from(file_nodes)
    for u, v, d in graph.edges(file_nodes, data=True):
        edge_type = d.get("edge_type", "imports")
        if (
            edge_type in _FILE_COMMUNITY_EDGE_TYPES
            and v in full_undirected
            and not full_undirected.has_edge(u, v)
        ):
            full_undirected.add_edge(u, v)

    # Labels and conductance come from the production members: tests were kept
    # out of the partition so they would not shape a community, and they must
    # not name it either. The non-core catch-all is labelled from what it has.
    label_members: dict[int, list[str]] = {}
    production_labels: dict[int, str] = {}
    for cid, members in community_members.items():
        sorted_members = sorted(members)
        prod_members = [m for m in sorted_members if not non_core[m]]
        label_members[cid] = prod_members or sorted_members
        label = known_labels.get(cid) or _heuristic_label(label_members[cid], cid, extra_generic)
        if prod_members:
            production_labels[cid] = label
        communities_info[cid] = CommunityInfo(
            community_id=cid,
            label=label,
            members=sorted_members,
            size=len(sorted_members),
            cohesion=_cohesion_score(full_undirected, sorted_members),
            dominant_language=_dominant_language(sorted_members, graph),
            conductance=_conductance(undirected, prod_members),
        )

    # Deduplicate labels — add sub-labels when multiple communities
    # share the same base (e.g. "web" → "web/components", "web/api")
    _deduplicate_labels(communities_info, extra_generic, label_members)

    return communities_info, production_labels


def _partition_production(undirected: nx.Graph) -> tuple[dict[str, int], str]:
    """``({production file: community}, algorithm)`` over the production link graph."""
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

    # Re-index by size descending. The rank becomes the community id, which
    # becomes a module page's target_path, so it has to be a total order:
    # size alone leaves same-size communities in partition-iteration order,
    # and Leiden (tried before Louvain) returns a partition dict ordered by
    # its own internals rather than by the node list we sorted above.
    split_lists.sort(key=lambda members: (-len(members), min(members)))

    prod_assignment: dict[str, int] = {}
    for cid, members in enumerate(split_lists):
        for node in members:
            prod_assignment[node] = cid
    return prod_assignment, algorithm


def file_community_labels(graph_builder: Any) -> dict[str, str]:
    """``{file path: community label}`` off *graph_builder*'s cached file communities.

    The refactoring detectors key foreign modules by this label, so every path
    that writes refactoring plans (init, update, re-score, ``repowise health``)
    must pass the same map or the same file gets a different plan. Empty when
    community detection fails.
    """
    try:
        assignment = graph_builder.community_detection()
        info = graph_builder.community_info()
    except Exception as exc:
        log.debug("file_community_labels_failed", error=str(exc))
        return {}
    labels: dict[str, str] = {}
    for node_id, community_id in assignment.items():
        label = getattr(info.get(community_id), "label", None)
        if label:
            labels[node_id] = label
    return labels


# ---------------------------------------------------------------------------
# Symbol-level community detection
# ---------------------------------------------------------------------------


def symbol_community_graph(graph: nx.DiGraph) -> nx.Graph:
    """The undirected call/heritage graph over every symbol node: the whole partition input."""
    # Sorted for the same reason detect_file_communities sorts its node list:
    # insertion order seeds the partition, and the graph's is not stable
    # between runs. This half was left unsorted when the file half was fixed.
    symbol_nodes = sorted(
        n for n, d in graph.nodes(data=True)
        if d.get("node_type") == "symbol"
    )
    symbol_set = frozenset(symbol_nodes)
    undirected = nx.Graph()
    undirected.add_nodes_from(symbol_nodes)
    undirected.add_edges_from(
        sorted(
            (u, v)
            for u, v, d in graph.edges(data=True)
            if d.get("edge_type") in _SYMBOL_COMMUNITY_EDGE_TYPES
            and u in symbol_set
            and v in symbol_set
        )
    )
    return undirected


def detect_symbol_communities(
    graph: nx.DiGraph, undirected: nx.Graph | None = None
) -> dict[str, int]:
    """Detect communities among symbol nodes using calls/heritage edges.

    *undirected* is :func:`symbol_community_graph` of *graph*, when the caller
    already built it. Returns {symbol_id: community_id}.
    """
    if undirected is None:
        undirected = symbol_community_graph(graph)
    symbol_nodes = list(undirected.nodes())
    if not symbol_nodes:
        return {}

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

    # Re-index by size descending, first member breaking ties (same total-order
    # argument as detect_file_communities).
    ordered = sorted(raw.values(), key=lambda members: (-len(members), min(members)))
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


def carry_symbol_communities(previous: dict[str, int], undirected: nx.Graph) -> dict[str, int]:
    """*previous* partition carried onto the current :func:`symbol_community_graph`.

    The rule a reused partition follows until the next full detection: a symbol
    keeps its community; a removed one drops out; a new one joins the community
    most of its linked symbols are in (ties to the lower id), or gets a
    community of its own when none of them was partitioned.
    """
    result = {node: previous[node] for node in undirected.nodes() if node in previous}
    next_cid = max(previous.values(), default=-1) + 1
    for node in sorted(n for n in undirected.nodes() if n not in previous):
        votes = Counter(previous[m] for m in undirected.adj[node] if m in previous)
        if votes:
            result[node] = min(votes, key=lambda cid: (-votes[cid], cid))
        else:
            result[node] = next_cid
            next_cid += 1
    return result
