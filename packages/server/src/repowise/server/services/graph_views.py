"""Community-level graph view builders — architecture super-graph + slice.

Pure service functions (no FastAPI imports): inputs are a DB session plus
query parameters, output is the exact response model the HTTP endpoints
serve. The graph routers are thin wrappers over these, and non-HTTP
consumers — e.g. an indexer precomputing per-snapshot artifacts — call the
SAME functions so both serving paths emit byte-identical shapes
(``model_dump(mode="json")`` for the artifact form).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ids import is_external
from repowise.core.ingestion.models import DOCUMENTATION_EDGE_TYPES, NON_CODE_EDGE_TYPES
from repowise.core.persistence import crud
from repowise.core.persistence.index_cache import IndexStampCache
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.support_paths import FilePopulation, file_population
from repowise.server.mcp_server._graph_utils import (
    community_cohesion,
    community_conductance,
    community_label,
)
from repowise.server.schemas import (
    ArchitectureEdgeResponse,
    ArchitectureGraphResponse,
    ArchitectureNodeResponse,
    CommunitySliceNodeResponse,
    CommunitySliceResponse,
    GraphEdgeResponse,
    PopulationBreakdown,
    UnclusteredFiles,
)
from repowise.server.services.node_signals import (
    EMPTY_SIGNALS,
    collect_node_signals,
    node_to_response,
)

# Cap on slice member nodes. Communities are small relative to the repo, but a
# few mega-clusters exist; this keeps the blossom payload in the 50-300 target
# band and the satellite layout responsive.
SLICE_MEMBER_CAP = 300
# Boundary stubs per slice: only the most-connected outside neighbors survive,
# so a blossom shows its strongest cross-cluster ties instead of a dust cloud.
SLICE_BOUNDARY_CAP = 40
# Chunk size for member-id IN lists, well under SQLite's 999-parameter limit.
_SLICE_IN_CHUNK = 400
# Members read before the population filter and any display cap apply.
# Ceiling: a larger community still reports this number. The largest indexed so
# far is under 800; past it the upgrade is a COUNT(*) plus a "counted over the
# top N" field, not a bigger constant.
MEMBER_READ_CAP = 2000
# Boundary candidates resolved (rows carry `is_test`) before the filter and cap.
# Ceiling: mostly-hidden neighbourhoods return fewer than SLICE_BOUNDARY_CAP.
_SLICE_BOUNDARY_CANDIDATES = SLICE_BOUNDARY_CAP * 3
# Head of the unclustered list carried on the architecture payload.
UNCLUSTERED_SAMPLE_CAP = 200
# The edge columns the views read. Selected as plain rows: hydrating ORM
# objects for every edge cost several times the query on a large graph.
_EDGE_COLUMNS = (
    GraphEdge.id,
    GraphEdge.source_node_id,
    GraphEdge.target_node_id,
    GraphEdge.imported_names_json,
    GraphEdge.edge_type,
    GraphEdge.confidence,
)


@dataclass(frozen=True)
class Population:
    """Which non-production populations a community view counts.

    Production is always in. Applied before anything is sized or ranked, so no
    number on the map describes files it does not draw.
    """

    include_tests: bool = False
    include_examples: bool = False
    include_docs: bool = False

    def keeps(self, kind: FilePopulation) -> bool:
        if kind == "test":
            return self.include_tests
        if kind == "example":
            return self.include_examples
        if kind == "doc":
            return self.include_docs
        return True


PRODUCTION_ONLY = Population()


def is_external_node(node: GraphNode) -> bool:
    """``external:`` and ``framework:`` rows are stored as files; they are not."""
    return is_external(node.node_id)


def bucket_by_community(
    all_nodes: list[GraphNode], visible: list[GraphNode]
) -> tuple[dict[int, list[GraphNode]], Counter[int]]:
    """Visible members per community, and how many each community hides."""
    visible_ids = {n.node_id for n in visible}
    buckets: dict[int, list[GraphNode]] = {}
    hidden: Counter[int] = Counter()
    for n in all_nodes:
        if is_external_node(n):
            continue
        cid = n.community_id if n.community_id is not None else 0
        if n.node_id in visible_ids:
            buckets.setdefault(cid, []).append(n)
        else:
            hidden[cid] += 1
    return buckets, hidden


def split_population(
    nodes: list[GraphNode], population: Population
) -> tuple[list[GraphNode], Counter[str]]:
    """Return the nodes *population* keeps, and a count of every kind seen.

    External nodes are dropped and not counted.
    """
    kept: list[GraphNode] = []
    counts: Counter[str] = Counter()
    for n in nodes:
        if is_external_node(n):
            continue
        kind = file_population(n.node_id, is_test=bool(n.is_test))
        counts[kind] += 1
        if population.keeps(kind):
            kept.append(n)
    return kept, counts


def population_breakdown(
    counts: Counter[str], visible: int, population: Population
) -> PopulationBreakdown:
    return PopulationBreakdown(
        total=sum(counts.values()),
        visible=visible,
        tests=counts["test"],
        examples=counts["example"],
        docs=counts["doc"],
        include_tests=population.include_tests,
        include_examples=population.include_examples,
        include_docs=population.include_docs,
    )


def _parse_imported_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def edge_response(e: GraphEdge | Row) -> GraphEdgeResponse:
    """Build a GraphEdgeResponse from a GraphEdge ORM row or a :data:`_EDGE_COLUMNS` row."""
    return GraphEdgeResponse(
        source=e.source_node_id,
        target=e.target_node_id,
        imported_names=_parse_imported_names(e.imported_names_json),
        edge_type=e.edge_type,
        confidence=e.confidence,
    )


# The node columns the code graph readers use: the exclude rule reads node_type
# and file_path, the no-path diagnostics read community_id and pagerank, the
# entry-point view reads is_entry_point.
_CODE_NODE_COLUMNS = (
    GraphNode.node_id,
    GraphNode.node_type,
    GraphNode.file_path,
    GraphNode.community_id,
    GraphNode.pagerank,
    GraphNode.is_entry_point,
)
# Entries, one per (repository, exclude rules) at the current index state;
# older states are dropped on rebuild. Each holds a whole graph in memory.
_code_graphs = IndexStampCache(4)
# One build per repository at a time, so a cold burst of requests loads once.
_code_graph_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class CodeGraph:
    """The repository's code graph after the exclude rules: read-only, shared.

    ``graph`` holds every surviving node and one arc per surviving code edge
    pair (``edge_type`` is the last row's). ``edges`` keeps every row, parallel
    edge types included, as :data:`_EDGE_COLUMNS` rows.
    """

    graph: Any
    nodes: dict[str, Row]
    edges: list[Row]


def clear_code_graph_cache() -> None:
    _code_graphs.clear()
    # A lock is bound to the loop that first waited on it.
    _code_graph_locks.clear()


def _spec_key(exclude_spec: Any) -> tuple[str, ...] | None:
    """The rules a spec was compiled from: equal rules share one cached graph."""
    if exclude_spec is None:
        return None
    return tuple(str(p.pattern) for p in exclude_spec.patterns)


async def load_code_graph(session: AsyncSession, repo_id: str, exclude_spec: Any) -> CodeGraph:
    """Every code node and edge of *repo_id*, minus excluded files, as a DiGraph.

    Non-code relations (history, prose) are left out: a ``co_changes`` edge
    records that two files move together, so it would be a free hop no code
    creates. Containment stays, since ``defines`` is the only bridge from the
    file layer to the symbol layer. Kept per index state and exclude rules;
    callers must not mutate the result.
    """
    key = await _code_graphs.key(session, repo_id, _spec_key(exclude_spec))
    if key in _code_graphs:
        return _code_graphs.get(key)
    async with _code_graph_locks.setdefault(repo_id, asyncio.Lock()):
        if key in _code_graphs:
            return _code_graphs.get(key)
        loaded = await _build_code_graph(session, repo_id, exclude_spec)
        # Older index states are never asked for again; other exclude rules
        # at this state stay.
        _code_graphs.drop_repo_except(repo_id, key[:3])
        return _code_graphs.remember(key, loaded)


async def _build_code_graph(session: AsyncSession, repo_id: str, exclude_spec: Any) -> CodeGraph:
    import networkx as nx

    from repowise.server.mcp_server._helpers import filter_graph_nodes

    node_rows = (
        await session.execute(select(*_CODE_NODE_COLUMNS).where(GraphNode.repository_id == repo_id))
    ).all()
    nodes = {n.node_id: n for n in filter_graph_nodes(node_rows, exclude_spec)}
    excluded = {n.node_id for n in node_rows} - nodes.keys()
    edge_rows = (
        await session.execute(
            select(*_EDGE_COLUMNS).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.edge_type.notin_(NON_CODE_EDGE_TYPES),
            )
        )
    ).all()
    if excluded:
        edge_rows = [
            e
            for e in edge_rows
            if e.source_node_id not in excluded and e.target_node_id not in excluded
        ]

    graph = nx.DiGraph()
    # Every indexed node, not just edge endpoints: a file whose only edges were
    # temporal is still indexed and must not read as "not found".
    graph.add_nodes_from(nodes)
    for e in edge_rows:
        graph.add_edge(e.source_node_id, e.target_node_id, edge_type=e.edge_type)
    return CodeGraph(graph, nodes, edge_rows)


async def build_architecture_graph(
    session: AsyncSession,
    repo_id: str,
    min_members: int = 2,
    population: Population = PRODUCTION_ONLY,
) -> ArchitectureGraphResponse:
    """High-level architecture view: one node per detected community.

    Edges between communities are weighted by the number of underlying file
    edges that cross the boundary. Each super-node also carries signal counts
    (hotspots, dead files, decisions, doc coverage) so the architecture view
    surfaces health at a glance.

    Every figure, the ranking and the ``min_members`` cut are computed over
    the *population*.
    """
    all_nodes = await crud.get_all_file_metrics(session, repo_id)
    if not all_nodes:
        return ArchitectureGraphResponse(nodes=[], edges=[])

    visible, counts = split_population(all_nodes, population)
    breakdown = population_breakdown(counts, len(visible), population)

    buckets, hidden_per_community = bucket_by_community(all_nodes, visible)
    node_to_community = {n.node_id: cid for cid, members in buckets.items() for n in members}

    # Pull cross-link signals once for the visible population so super-nodes
    # can aggregate hotspot/dead/decision counts without N round-trips.
    signals = await collect_node_signals(session, repo_id, [n.node_id for n in visible])

    arch_nodes: list[ArchitectureNodeResponse] = []
    unclustered: list[GraphNode] = []
    for cid, members in buckets.items():
        if len(members) < min_members:
            unclustered.extend(members)
            continue
        top = max(members, key=lambda m: m.pagerank or 0.0)
        hotspot_count = 0
        dead_count = 0
        has_decision = False
        doc_hits = 0
        langs: dict[str, int] = {}
        for m in members:
            sig = signals.get(m.node_id, EMPTY_SIGNALS)
            if sig.is_hotspot:
                hotspot_count += 1
            if sig.is_dead:
                dead_count += 1
            if sig.has_decision:
                has_decision = True
            if sig.has_doc:
                doc_hits += 1
            if m.language:
                langs[m.language] = langs.get(m.language, 0) + 1
        top_langs = [lang for lang, _ in sorted(langs.items(), key=lambda kv: -kv[1])[:3]]
        avg_pr = sum(m.pagerank or 0.0 for m in members) / max(len(members), 1)

        arch_nodes.append(
            ArchitectureNodeResponse(
                community_id=cid,
                label=community_label(top),
                cohesion=community_cohesion(top),
                conductance=community_conductance(top),
                member_count=len(members),
                hidden_member_count=hidden_per_community[cid],
                top_file=top.node_id,
                avg_pagerank=avg_pr,
                hotspot_count=hotspot_count,
                dead_count=dead_count,
                has_decision=has_decision,
                doc_coverage_pct=doc_hits / max(len(members), 1),
                languages=top_langs,
            )
        )

    arch_nodes.sort(key=lambda a: -a.member_count)
    kept_communities = {a.community_id for a in arch_nodes}

    # Collapse cross-community edges; a doc link or co-change across is not coupling.
    edge_result = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type.notin_(NON_CODE_EDGE_TYPES),
        )
    )
    edge_counts: dict[tuple[int, int], int] = {}
    for source, target in edge_result.all():
        src_c = node_to_community.get(source)
        tgt_c = node_to_community.get(target)
        if src_c is None or tgt_c is None or src_c == tgt_c:
            continue
        if src_c not in kept_communities or tgt_c not in kept_communities:
            continue
        key = (src_c, tgt_c)
        edge_counts[key] = edge_counts.get(key, 0) + 1

    # Sorted, so the payload does not depend on the order the rows were read in.
    arch_edges = [
        ArchitectureEdgeResponse(source=s, target=t, edge_count=c)
        for (s, t), c in sorted(edge_counts.items())
    ]

    unclustered.sort(key=lambda n: (-(n.pagerank or 0.0), n.node_id))
    return ArchitectureGraphResponse(
        nodes=arch_nodes,
        edges=arch_edges,
        population=breakdown,
        unclustered=UnclusteredFiles(
            file_count=len(unclustered),
            files=[n.node_id for n in unclustered[:UNCLUSTERED_SAMPLE_CAP]],
        ),
    )


async def neighbour_edge_counts(
    session: AsyncSession,
    repo_id: str,
    community_id: int,
    member_ids: list[str],
    population: Population = PRODUCTION_ONLY,
) -> list[tuple[int, int]]:
    """``[(community_id, edge_count)]`` for the communities *member_ids* depend on.

    Outbound file edges to another community, both ends in the *population*,
    most-connected first. ``crud.get_cross_community_edges`` is the unfiltered
    SQL form.
    """
    if not member_ids:
        return []
    member_set = set(member_ids)
    targets: Counter[str] = Counter()
    for start in range(0, len(member_ids), _SLICE_IN_CHUNK):
        chunk = member_ids[start : start + _SLICE_IN_CHUNK]
        rows = await session.execute(
            select(GraphEdge.target_node_id).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.source_node_id.in_(chunk),
                GraphEdge.edge_type.notin_(NON_CODE_EDGE_TYPES),
            )
        )
        for (target,) in rows.all():
            if target not in member_set:
                targets[target] += 1

    outside = await crud.get_graph_nodes_by_ids(session, repo_id, list(targets))
    per_community: Counter[int] = Counter()
    for target, count in targets.items():
        node = outside.get(target)
        if node is None or node.node_type != "file" or node.community_id == community_id:
            continue
        if is_external_node(node):
            continue
        if not population.keeps(file_population(node.node_id, is_test=bool(node.is_test))):
            continue
        per_community[node.community_id] += count
    return sorted(per_community.items(), key=lambda kv: (-kv[1], kv[0]))


async def build_community_slice(
    session: AsyncSession,
    repo_id: str,
    community_id: int,
    member_limit: int = SLICE_MEMBER_CAP,
    population: Population = PRODUCTION_ONLY,
    cut: dict | None = None,
) -> CommunitySliceResponse:
    """Return a single community's sub-graph for the constellation blossom.

    Payload = the community's member file nodes, the edges among them, and a
    thin ring of one-hop *boundary stubs*: neighbor nodes outside the community
    that share an edge with a member, returned as minimal nodes flagged
    ``is_boundary=true`` so cross-cluster edges can render without dragging the
    whole neighbor cluster in. Sized to ~50-300 nodes.

    Members and boundary stubs are both filtered to the *population*. *cut*
    is the dead-code cross-repo cut's inputs (``deps.cross_repo_cut_inputs``).
    """
    all_members = await crud.get_community_members(
        session, repo_id, community_id, node_type="file", limit=MEMBER_READ_CAP
    )
    members, counts = split_population(all_members, population)
    hidden_member_count = sum(counts.values()) - len(members)
    visible_member_count = len(members)  # before the display cap
    truncated = visible_member_count > member_limit
    if truncated:
        members = members[:member_limit]
    member_ids = {m.node_id for m in members}

    # Edges touching any member: among-members stay; member<->outside become
    # cross-cluster links that pull in a boundary stub for the outside endpoint.
    # The membership filter is pushed into SQL so we never load the whole
    # repo's edge table into Python; the IN lists are chunked under SQLite's
    # parameter limit. The Python filter below is kept as-is for correctness —
    # the SQL only bounds the rows fetched (a superset-or-equal of what the
    # Python filter keeps). Source and target are read in two queries so each
    # uses its own index: SQLite planned ``source IN .. OR target IN ..`` as a
    # scan of every edge in the repo.
    member_id_list = list(member_ids)
    edge_rows: dict[str, Row] = {}
    for start in range(0, len(member_id_list), _SLICE_IN_CHUNK):
        chunk = member_id_list[start : start + _SLICE_IN_CHUNK]
        for endpoint in (GraphEdge.source_node_id, GraphEdge.target_node_id):
            chunk_result = await session.execute(
                select(*_EDGE_COLUMNS).where(
                    GraphEdge.repository_id == repo_id,
                    endpoint.in_(chunk),
                    # The UI has no legend for doc links yet.
                    GraphEdge.edge_type.notin_(DOCUMENTATION_EDGE_TYPES),
                )
            )
            # Dedup: an edge with both endpoints in the members matches twice.
            # The PK keeps each edge once.
            for e in chunk_result.all():
                edge_rows[e.id] = e

    kept_edges: list[Row] = []
    boundary_degree: dict[str, int] = {}
    for e in edge_rows.values():
        src_in = e.source_node_id in member_ids
        tgt_in = e.target_node_id in member_ids
        if not src_in and not tgt_in:
            continue
        kept_edges.append(e)
        # External stubs are ranked out here, not after the cap, so they
        # cannot take the boundary slots from real files.
        if not src_in and not is_external(e.source_node_id):
            boundary_degree[e.source_node_id] = boundary_degree.get(e.source_node_id, 0) + 1
        if not tgt_in and not is_external(e.target_node_id):
            boundary_degree[e.target_node_id] = boundary_degree.get(e.target_node_id, 0) + 1

    # Cap boundary stubs to the most-connected neighbors: hub communities can
    # touch thousands of outside files, which would flood the blossom with
    # dust. Edges to dropped stubs are filtered by the visible_ids pass below.
    candidate_ids = sorted(boundary_degree, key=lambda n: (-boundary_degree[n], n))[
        :_SLICE_BOUNDARY_CANDIDATES
    ]

    # Resolve boundary stub rows (minimal: just need a node row to render).
    boundary_nodes: list[GraphNode] = []
    if candidate_ids:
        stub_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_id.in_(candidate_ids),
            )
        )
        by_id = {n.node_id: n for n in stub_result.scalars()}
        kept, _ = split_population(
            [by_id[n] for n in candidate_ids if n in by_id], population
        )
        boundary_nodes = kept[:SLICE_BOUNDARY_CAP]
    resolved_boundary = {n.node_id for n in boundary_nodes}

    # Drop edges whose outside endpoint has no resolvable node (orphan ref).
    visible_ids = member_ids | resolved_boundary
    # Sorted, so the payload does not depend on the order the rows were read in.
    links = [
        edge_response(e)
        for e in sorted(
            kept_edges, key=lambda e: (e.source_node_id, e.target_node_id, e.edge_type)
        )
        if e.source_node_id in visible_ids and e.target_node_id in visible_ids
    ]

    # Member signals only (boundary stubs stay minimal / all-false signals).
    signals = await collect_node_signals(session, repo_id, list(member_ids), **(cut or {}))
    nodes = [
        node_to_response(m, signals.get(m.node_id, EMPTY_SIGNALS), CommunitySliceNodeResponse)
        for m in members
    ]
    nodes.extend(
        node_to_response(n, EMPTY_SIGNALS, CommunitySliceNodeResponse, is_boundary=True)
        for n in boundary_nodes
    )

    return CommunitySliceResponse(
        nodes=nodes,
        links=links,
        community_id=community_id,
        member_count=visible_member_count,
        truncated=truncated,
        hidden_member_count=hidden_member_count,
    )
