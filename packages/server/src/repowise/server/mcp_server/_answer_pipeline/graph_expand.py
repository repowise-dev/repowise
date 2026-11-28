"""Graph expansion: 1-hop neighbours of the top hits join the candidate set."""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.exclusion import is_excluded, spec_key
from repowise.core.ingestion.models import CONTAINMENT_EDGE_TYPES
from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.mcp_server._graph_files import (
    is_symbol_node,
    keep_projected_edge,
    per_index,
)
from repowise.server.mcp_server._page_paths import PAGELESS_FILE

# Seeds to expand from, and the most files expansion may add.
_GRAPH_EXPAND_TOP_N = 2
_GRAPH_EXPAND_MAX_NEW = 3

# A neighbour above max(floor, p99) degree is a hub: near everything, so it
# says nothing about this question, yet PageRank would rank it first.
_HUB_DEGREE_FLOOR = 50
_HUB_DEGREE_PERCENTILE = 0.99

# Expanded hits score below their parent but can pass the tail of the top 5.
_GRAPH_EXPAND_DAMPING = 0.7


def _projected_pair(
    src: str | None, tgt: str | None, edge_type: str | None, confidence: float | None
) -> tuple[str | None, str | None]:
    """An edge's two endpoints as file paths, or ``(None, None)`` to drop it.

    Only symbol-layer edges are guarded; file-layer edges such as a module's
    ``co_changes`` with its docs pass as they are.
    """
    src = src or ""
    tgt = tgt or ""
    if not is_symbol_node(src) and not is_symbol_node(tgt):
        return (src or None, tgt or None)
    src_file, tgt_file = file_of_symbol(src), file_of_symbol(tgt)
    if not keep_projected_edge(src_file, tgt_file, edge_type, confidence):
        return (None, None)
    return (src_file, tgt_file)


async def _projected_edges(session: Any, repo_id: str) -> list[tuple[str, str]]:
    """Every non-containment edge for *repo_id*, as a pair of file paths.

    One scan projected in Python: a symbol-layer prefix match cannot use the
    node-id index. Containment always projects to a self-loop.
    """
    res = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.confidence,
        ).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type.notin_(CONTAINMENT_EDGE_TYPES),
        )
    )
    pairs: list[tuple[str, str]] = []
    for src, tgt, etype, conf in res.all():
        src_file, tgt_file = _projected_pair(src, tgt, etype, conf)
        if src_file and tgt_file:
            pairs.append((src_file, tgt_file))
    return pairs


def _hub_degree_cutoff(degree: dict[str, int]) -> int:
    """Degree at which a candidate counts as a hub: ``max(floor, p99)`` of the set."""
    if not degree:
        return 1 << 30
    values = sorted(degree.values())
    idx = min(len(values) - 1, int(len(values) * _HUB_DEGREE_PERCENTILE))
    return max(_HUB_DEGREE_FLOOR, values[idx])


class _ProjectedGraph(NamedTuple):
    """The projected edges as each file's neighbours, and every file's degree."""

    neighbours: dict[str, set[str]]
    degree: dict[str, int]


async def _projected_graph(
    session: Any, repo_id: str, exclude_spec: Any = None
) -> _ProjectedGraph:
    """:func:`_projected_edges` indexed by file, so a question reads its seeds'
    neighbours instead of walking every edge. With *exclude_spec*, a pair with
    an excluded file is left out of both the neighbours and the degrees."""
    neighbours: dict[str, set[str]] = {}
    degree: dict[str, int] = {}
    for src_file, tgt_file in await _projected_edges(session, repo_id):
        if is_excluded(src_file, exclude_spec) or is_excluded(tgt_file, exclude_spec):
            continue
        degree[src_file] = degree.get(src_file, 0) + 1
        degree[tgt_file] = degree.get(tgt_file, 0) + 1
        neighbours.setdefault(src_file, set()).add(tgt_file)
        neighbours.setdefault(tgt_file, set()).add(src_file)
    return _ProjectedGraph(neighbours, degree)


async def projected_graph(
    session: Any, repo_id: str, exclude_spec: Any = None
) -> _ProjectedGraph:
    """:func:`_projected_graph`, built once per index state. Read-only.

    The unfiltered graph is what goes to disk. A filtered one is kept in memory
    per rule set, so hub degrees count only edges between allowed files.
    """
    if not exclude_spec:
        return await per_index(
            session,
            repo_id,
            "projected_graph",
            lambda: _projected_graph(session, repo_id),
            persist=True,
        )
    return await per_index(
        session,
        repo_id,
        f"projected_graph:{hash(spec_key(exclude_spec))}",
        lambda: _projected_graph(session, repo_id, exclude_spec),
    )


def _seed_neighbors(
    graph: _ProjectedGraph, seed_set: set[str], existing: set[Any], exclude_spec: Any = None
) -> set[str]:
    """Files one hop from a seed, not yet a candidate and not excluded.

    Excluded files leave here, before hub ranking and the additions cap, so
    they never take a slot an allowed neighbour would have filled.
    """
    return {
        theirs
        for seed in seed_set
        for theirs in graph.neighbours.get(seed, ())
        if theirs not in existing and not is_excluded(theirs, exclude_spec)
    }


async def _neighbor_pages(
    session: Any, neighbors: set[str]
) -> tuple[list[Any], dict[str, float]]:
    """File-page rows of *neighbors*, and each neighbour's PageRank."""
    page_res = await session.execute(
        select(Page.target_path, Page.summary, Page.page_type).where(
            Page.target_path.in_(sorted(neighbors)),
            Page.page_type == "file_page",
        )
    )
    page_rows = list(page_res.all())

    pr_res = await session.execute(
        select(GraphNode.node_id, GraphNode.pagerank).where(
            GraphNode.node_id.in_(sorted(neighbors)),
            GraphNode.node_type == "file",
        )
    )
    pr_by_path = {row[0]: float(row[1] or 0.0) for row in pr_res.all()}
    return page_rows, pr_by_path


def _drop_hubs(page_rows: list[Any], degree: dict[str, int]) -> list[Any]:
    """*page_rows* without hubs, or all of them when every row is a hub."""
    # Cutoff over the candidates on offer, not the whole repo.
    cutoff = _hub_degree_cutoff({row[0]: degree.get(row[0], 0) for row in page_rows})
    non_hub = [row for row in page_rows if degree.get(row[0], 0) <= cutoff]
    return non_hub or page_rows


def _expansion_candidates(
    seeds: list[dict], page_rows: list[Any], pr_by_path: dict[str, float]
) -> list[dict]:
    """One damped hit per neighbour page, most central first."""
    strongest_parent = max(
        seeds, key=lambda hit: hit.get("score", 0.0), default={}
    )
    parent_score = strongest_parent.get("score", 0.0)
    confidence_factor = strongest_parent.get("_confidence_score_factor")
    candidates: list[dict] = []
    for path, summary, page_type in page_rows:
        candidate = {
            "page_id": f"file_page:{path}",
            "target_path": path,
            "title": f"File: {path}",
            "summary": summary or "",
            "snippet": (summary or "")[:200],
            "page_type": page_type or "file_page",
            "score": parent_score * _GRAPH_EXPAND_DAMPING,
            "_sources": {"graph_expand"},
            "_expanded_from": "graph",
            "_pagerank": pr_by_path.get(path, 0.0),
        }
        if confidence_factor is not None:
            candidate["_confidence_score_factor"] = confidence_factor
        candidates.append(candidate)

    # Path breaks PageRank ties so the pick does not follow database row order.
    candidates.sort(key=lambda c: (-c.get("_pagerank", 0.0), c["target_path"]))
    return candidates


async def expand_via_graph(
    hits: list[dict], ctx: Any, repo_id: str, exclude_spec: Any = None
) -> list[dict]:
    """Add up to ``_GRAPH_EXPAND_MAX_NEW`` one-hop neighbours of the top hits.

    Recovers the right file when retrieval landed next to it (a consumer
    instead of the implementation). Only neighbours with a file page that are
    not yet candidates join, marked ``_expanded_from`` with a damped score.
    """
    if not hits:
        return hits
    # Seeded from pages only, so a pageless file cannot change what pages add.
    seeds = [h for h in hits if h.get("page_type") != PAGELESS_FILE][:_GRAPH_EXPAND_TOP_N]
    seed_paths = [h.get("target_path") for h in seeds if h.get("target_path")]
    if not seed_paths:
        return hits
    existing = {h.get("target_path") for h in hits}

    async with get_session(ctx.session_factory) as session:
        # Every edge type, co-changes included, projected from the symbol layer
        # too so call edges reach the seeds.
        graph = await projected_graph(session, repo_id, exclude_spec)
        neighbors = _seed_neighbors(graph, set(seed_paths), existing, exclude_spec)
        if not neighbors:
            return hits
        page_rows, pr_by_path = await _neighbor_pages(session, neighbors)

    if not page_rows:
        return hits

    page_rows = _drop_hubs(page_rows, graph.degree)
    additions = _expansion_candidates(seeds, page_rows, pr_by_path)[:_GRAPH_EXPAND_MAX_NEW]
    if not additions:
        return hits

    combined = hits + additions
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined
