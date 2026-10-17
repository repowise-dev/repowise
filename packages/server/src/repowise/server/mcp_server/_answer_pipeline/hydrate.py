"""Hydrate hits with Page metadata, then bias them by PageRank."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server._page_paths import PAGELESS_FILE, pageless_path

_log = logging.getLogger("repowise.mcp.answer")

# PageRank bias is multiplicative and capped. We don't want a marginally
# more central file to outrank a strong text match — only to break ties.
# Empirically PageRank values on this corpus span ~0 to ~0.01; we normalise
# to the max in the candidate set and scale to a [1.0, 1.3] multiplier.
_PAGERANK_BIAS_MAX = 0.3


# ---------------------------------------------------------------------------
# Stage 2: Hydrate hits with Page metadata (target_path, summary)
# ---------------------------------------------------------------------------


async def _page_meta_by_id(ctx: Any, page_ids: list[str]) -> dict[str, dict[str, str]]:
    """Path, summary, type and freshness of each page id that has a page row."""
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(
                Page.id,
                Page.target_path,
                Page.summary,
                Page.page_type,
                Page.freshness_status,
            ).where(Page.id.in_(page_ids))
        )
        return {
            row[0]: {
                "target_path": row[1] or "",
                "summary": row[2] or "",
                "page_type": row[3] or "",
                "freshness": row[4] or "",
            }
            for row in res.all()
        }


def _hit_meta(hit: dict, meta_by_id: dict[str, dict[str, str]]) -> dict[str, str] | None:
    """The page metadata behind *hit*, or None when there is nothing to cite."""
    meta = meta_by_id.get(hit["page_id"])
    if meta is None and hit.get("page_type") == PAGELESS_FILE:
        # An indexed file with no page: its path is in its id, and it has
        # no summary to give, so it serves its matched code instead.
        meta = {"target_path": pageless_path(hit["page_id"]), "page_type": PAGELESS_FILE}
    return meta


def _apply_meta(hit: dict, meta: dict[str, str], scope: str | None) -> bool:
    """Copy *meta* onto *hit*; False when the hit is tombstoned or out of scope."""
    # Tombstoned pages document deleted/renamed files — serving them as
    # answer material would cite code that no longer exists.
    if meta.get("freshness") == "tombstone":
        return False
    target_path = meta.get("target_path", "")
    if scope and target_path and not target_path.startswith(scope):
        return False
    hit["target_path"] = target_path
    hit["summary"] = meta.get("summary", "")
    # Prefer the Page table's page_type when present — it's the source
    # of truth; retrievers sometimes carry stale or empty types.
    hit["page_type"] = meta.get("page_type") or hit.get("page_type", "")
    return True


async def hydrate_hits(hits: list[dict], ctx: Any, *, scope: str | None = None) -> list[dict]:
    """Attach target_path, summary, and page_type from the Page table.

    Mutates each hit in place. Applies the ``scope`` filter (path prefix) at
    this stage rather than during retrieval — retrievers don't know about
    paths, and post-filtering keeps the merge logic source-agnostic.
    """
    if not hits:
        return hits
    meta_by_id = await _page_meta_by_id(ctx, [h["page_id"] for h in hits])

    out: list[dict] = []
    pageless = 0
    for h in hits:
        meta = _hit_meta(h, meta_by_id)
        if meta is None:
            # A retrieved id with no page behind it: a decision vector, or a
            # vector left over from a page the stores have since disagreed
            # about. Either way there is nothing to cite and nothing to read,
            # and keeping it costs a served slot — while every later gate that
            # would have caught it (tombstone, scope) is keyed on a path it
            # does not have.
            pageless += 1
            continue
        if _apply_meta(h, meta, scope):
            out.append(h)
    if pageless:
        _log.warning(
            "get_answer dropped %d of %d retrieved hit(s) with no page row behind them; "
            "an id in a retriever that the page table does not know is either a "
            "non-page vector or a three-store disagreement (repowise doctor --repair)",
            pageless,
            len(hits),
        )
    return out


# ---------------------------------------------------------------------------
# Stage 3: PageRank bias
# ---------------------------------------------------------------------------


async def _file_pageranks(ctx: Any, paths: list[str]) -> dict[str, float]:
    """PageRank of each file node among *paths*."""
    async with get_session(ctx.session_factory) as session:
        # Look up GraphNodes by node_id — file nodes are keyed by their path.
        res = await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.node_id.in_(paths),
                GraphNode.node_type == "file",
            )
        )
        return {row[0]: float(row[1] or 0.0) for row in res.all()}


def _max_page_rank(hits: list[dict], pr_by_path: dict[str, float]) -> float:
    """The PageRank the bias normalises against."""
    # Normalised over the pages, so a file with no page cannot change their bias.
    paged = {h.get("target_path") for h in hits if h.get("page_type") != PAGELESS_FILE}
    max_pr = max((pr for path, pr in pr_by_path.items() if path in paged), default=0.0)
    return max_pr or max(pr_by_path.values(), default=0.0)


async def apply_pagerank_bias(hits: list[dict], ctx: Any) -> None:
    """Multiply each hit's score by a damped PageRank factor (in place).

    Looks up the GraphNode row for each hit's target_path and pulls its
    PageRank. We normalise within the candidate set rather than against the
    whole graph: a candidate with the highest PageRank among its peers gets
    the full bias, the lowest gets none. This avoids the failure mode where
    an absolute scale would over-reward famous-but-irrelevant files.
    """
    if not hits:
        return
    paths = [h.get("target_path") for h in hits if h.get("target_path")]
    if not paths:
        return
    pr_by_path = await _file_pageranks(ctx, paths)
    if not pr_by_path:
        return
    max_pr = _max_page_rank(hits, pr_by_path)
    if max_pr <= 0:
        return

    for h in hits:
        pr = pr_by_path.get(h.get("target_path"), 0.0)
        # Normalised in [0, 1] then scaled to a multiplicative bias in
        # [1.0, 1 + _PAGERANK_BIAS_MAX].
        bias = 1.0 + _PAGERANK_BIAS_MAX * min(pr / max_pr, 1.0)
        h["_pagerank"] = pr
        h["_pagerank_bias"] = round(bias, 3)
        h["score"] = h.get("score", 0.0) * bias

    hits.sort(key=lambda h: h["score"], reverse=True)
