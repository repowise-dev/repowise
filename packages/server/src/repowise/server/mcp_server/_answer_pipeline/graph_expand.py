"""Graph expansion: 1-hop neighbours of the top hits join the candidate set."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.ingestion.models import CONTAINMENT_EDGE_TYPES
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.mcp_server._graph_files import (
    is_symbol_node,
    keep_projected_edge,
    node_to_file,
    per_index,
)
from repowise.server.mcp_server._page_paths import PAGELESS_FILE

# Cap how many extra files graph expansion can add. Without a cap, a hub
# file (many importers) would flood the candidate set and dilute the LLM's
# context budget on tangential neighbors.
_GRAPH_EXPAND_TOP_N = 2
_GRAPH_EXPAND_MAX_NEW = 3

# Degree above which a neighbour is treated as a hub and dropped from the
# expansion set. A file that half the repo imports is a near-neighbour of
# everything, so it says nothing about *this* question; it is also exactly the
# file PageRank ranks first, which is why the ordering below needs a guard
# rather than trusting centrality on its own. Floor plus percentile, so a small
# repo where every file has a handful of edges excludes nothing.
_HUB_DEGREE_FLOOR = 50
_HUB_DEGREE_PERCENTILE = 0.99

# Damping factor for graph-expanded hits. They didn't surface in retrieval,
# so we trust them less than direct hits — but enough to outrank the bottom
# of the top-5 if the parent was strong. 0.7 keeps a strong parent's child
# (e.g. parent at 4.5, expanded child at 3.15) competitive with a real
# rank-3/4 hit (~3.0-3.5).
_GRAPH_EXPAND_DAMPING = 0.7


# ---------------------------------------------------------------------------
# Stage 4: Graph expansion (1-hop neighbors of top hits)
# ---------------------------------------------------------------------------


def _projected_pair(
    src: str | None, tgt: str | None, edge_type: str | None, confidence: float | None
) -> tuple[str | None, str | None]:
    """An edge's two endpoints as file paths, or ``(None, None)`` to drop it.

    Only the symbol layer gets the guards; the file layer is already
    file-to-file and keeps today's behaviour. The asymmetry is deliberate — the
    guards refuse cross-extension pairs, and a ``co_changes`` edge between a
    module and its docs is a "read this too" expansion has always served.
    """
    src = src or ""
    tgt = tgt or ""
    if not is_symbol_node(src) and not is_symbol_node(tgt):
        return (src or None, tgt or None)
    src_file, tgt_file = node_to_file(src), node_to_file(tgt)
    if not keep_projected_edge(src_file, tgt_file, edge_type, confidence):
        return (None, None)
    return (src_file, tgt_file)


async def _projected_edges(session: Any, repo_id: str) -> list[tuple[str, str]]:
    """Every non-containment edge for *repo_id*, as a pair of file paths.

    One scan, projected in Python. A prefix match on the symbol layer cannot use
    the node-id index (SQLite disables the LIKE optimisation under an ESCAPE
    clause), so per-candidate queries were a scan each; this is one.

    Containment is excluded because both its endpoints describe the same file,
    so a projected containment edge is always a self-loop.
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
    """Degree at which a candidate counts as a hub, for this candidate set.

    ``max(floor, p99)``. The floor keeps a small or sparsely-linked repo from
    excluding ordinary files, and the percentile keeps a densely-linked one
    from excluding nothing.
    """
    if not degree:
        return 1 << 30
    values = sorted(degree.values())
    idx = min(len(values) - 1, int(len(values) * _HUB_DEGREE_PERCENTILE))
    return max(_HUB_DEGREE_FLOOR, values[idx])


def _seed_neighbors(
    pairs: list[tuple[str, str]], seed_set: set[str], existing: set[Any]
) -> tuple[set[str], dict[str, int]]:
    """Files one hop from a seed and not yet a candidate, plus every file's degree."""
    neighbors: set[str] = set()
    degree: dict[str, int] = {}
    for src_file, tgt_file in pairs:
        degree[src_file] = degree.get(src_file, 0) + 1
        degree[tgt_file] = degree.get(tgt_file, 0) + 1
        for mine, theirs in ((src_file, tgt_file), (tgt_file, src_file)):
            if mine in seed_set and theirs not in existing:
                neighbors.add(theirs)
    return neighbors, degree


async def _neighbor_pages(
    session: Any, neighbors: set[str]
) -> tuple[list[Any], dict[str, float]]:
    """File-page rows of *neighbors*, and each neighbour's PageRank."""
    # Only fold in neighbors that have a wiki page — otherwise the LLM
    # context block can't carry a useful excerpt for them.
    page_res = await session.execute(
        select(Page.target_path, Page.summary, Page.page_type).where(
            Page.target_path.in_(sorted(neighbors)),
            Page.page_type == "file_page",
        )
    )
    page_rows = list(page_res.all())

    # Also load PageRank for the neighbors so we can rank them.
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
    # Drop hubs before ranking. Ranking by PageRank alone actively prefers
    # them, which is the wrong instinct here: expansion is trying to name the
    # specific file the question is about, and the most-imported file in the
    # repo is the least specific candidate available.
    # Scoped to the candidate set, not the repo: the cutoff answers "is this
    # neighbour a hub among the ones on offer".
    cutoff = _hub_degree_cutoff({row[0]: degree.get(row[0], 0) for row in page_rows})
    non_hub = [row for row in page_rows if degree.get(row[0], 0) <= cutoff]
    return non_hub or page_rows


def _expansion_candidates(
    seeds: list[dict], page_rows: list[Any], pr_by_path: dict[str, float]
) -> list[dict]:
    """One damped hit per neighbour page, most central first."""
    # Damp parent score by _GRAPH_EXPAND_DAMPING for child candidates; pick
    # the strongest parent each child connects to (taking the max parent
    # score is conservative — favors well-connected neighbors).
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

    # Rank candidates by PageRank within the expansion set so we pick the
    # most central neighbor first when we have multiple plausible ones.
    # Path breaks ties, so which neighbour is picked cannot move with the
    # database's row order between runs on identical data.
    candidates.sort(key=lambda c: (-c.get("_pagerank", 0.0), c["target_path"]))
    return candidates


async def expand_via_graph(hits: list[dict], ctx: Any, repo_id: str) -> list[dict]:
    """Add up to ``_GRAPH_EXPAND_MAX_NEW`` graph-neighbor files to ``hits``.

    Rescues near-misses where the top retrieved file is in the right
    neighborhood but isn't the actual answer (a consumer instead of the
    orchestrator, a wrapper instead of the implementation). Expansion walks
    one hop through GraphEdges in both directions from the top-N candidates,
    then folds in any neighbor that:
      * has a wiki page (otherwise the LLM has nothing to read)
      * is not already in the candidate set

    Expanded hits carry an ``_expanded_from`` marker and a damped score so
    the gate / confidence calibration knows they're indirect.
    """
    if not hits:
        return hits
    # Seeded from pages only, so a file with no page cannot change what pages add.
    seeds = [h for h in hits if h.get("page_type") != PAGELESS_FILE][:_GRAPH_EXPAND_TOP_N]
    seed_paths = [h.get("target_path") for h in seeds if h.get("target_path")]
    if not seed_paths:
        return hits
    existing = {h.get("target_path") for h in hits}

    async with get_session(ctx.session_factory) as session:
        # Not import edges only, despite the naming this code used to carry:
        # ``co_changes`` neighbours stay in on purpose, because "this file moves
        # with yours" is a genuine read-this-too signal, and expansion surfaces
        # what it adds neutrally as ``[graph-expanded]`` rather than as an import
        # claim.
        #
        # The scan reaches the symbol layer as well as the file layer: ``calls``
        # and its siblings join ``path::Name`` nodes, so an equality test against
        # a seed path matched none of them and the call graph was invisible here.
        pairs = await per_index(
            session, repo_id, "projected_edges", lambda: _projected_edges(session, repo_id)
        )
        neighbors, degree = _seed_neighbors(pairs, set(seed_paths), existing)
        if not neighbors:
            return hits
        page_rows, pr_by_path = await _neighbor_pages(session, neighbors)

    if not page_rows:
        return hits

    page_rows = _drop_hubs(page_rows, degree)
    additions = _expansion_candidates(seeds, page_rows, pr_by_path)[:_GRAPH_EXPAND_MAX_NEW]
    if not additions:
        return hits

    combined = hits + additions
    combined.sort(key=lambda h: h["score"], reverse=True)
    return combined
