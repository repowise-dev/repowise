"""Hydrate hits with Page metadata, then bias them by PageRank."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server._page_paths import PAGELESS_FILE, pageless_path

_log = logging.getLogger("repowise.mcp.answer")

# Capped multiplicative bias, [1.0, 1.3]: breaks ties, never beats a strong match.
_PAGERANK_BIAS_MAX = 0.3


def _parse_section_meta(raw_meta: str | None, m: dict[str, Any]) -> None:
    if not raw_meta:
        return
    try:
        sec_meta = json.loads(raw_meta)
    except (json.JSONDecodeError, TypeError):
        return
    if isinstance(sec_meta, dict):
        if "section_id" in sec_meta:
            m["section_id"] = sec_meta["section_id"]
        if "heading_trail" in sec_meta:
            m["heading_trail"] = sec_meta["heading_trail"]


async def _page_meta_by_id(ctx: Any, page_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Path, summary, type and freshness of each page id that has a page row."""
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(
                Page.id,
                Page.target_path,
                Page.summary,
                Page.page_type,
                Page.freshness_status,
                Page.metadata_json,
            ).where(Page.id.in_(page_ids))
        )
        out: dict[str, dict[str, Any]] = {}
        for row in res.all():
            m: dict[str, Any] = {
                "target_path": row[1] or "",
                "summary": row[2] or "",
                "page_type": row[3] or "",
                "freshness": row[4] or "",
            }
            if row[3] == "document_section":
                _parse_section_meta(row[5], m)
            out[row[0]] = m
        return out


def _hit_meta(hit: dict, meta_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The page metadata behind *hit*, or None when there is nothing to cite."""
    meta = meta_by_id.get(hit["page_id"])
    if meta is None and hit.get("page_type") == PAGELESS_FILE:
        # A pageless file: its path is in its id, and it serves matched code.
        meta = {"target_path": pageless_path(hit["page_id"]), "page_type": PAGELESS_FILE}
    return meta


def _apply_meta(hit: dict, meta: dict[str, Any], scope: str | None) -> bool:
    """Copy *meta* onto *hit*; False when the hit is tombstoned or out of scope."""
    # Tombstoned pages describe deleted or renamed files.
    if meta.get("freshness") == "tombstone":
        return False
    target_path = meta.get("target_path", "")
    if scope and target_path and not target_path.startswith(scope):
        return False
    hit["target_path"] = target_path
    hit["summary"] = meta.get("summary", "")
    # The page table's type wins; retrievers can carry stale ones.
    hit["page_type"] = meta.get("page_type") or hit.get("page_type", "")
    if "section_id" in meta:
        hit["section_id"] = meta["section_id"]
    if "heading_trail" in meta:
        hit["heading_trail"] = meta["heading_trail"]
    return True


async def hydrate_hits(hits: list[dict], ctx: Any, *, scope: str | None = None) -> list[dict]:
    """Attach path, summary and page type in place.

    Drops hits with no page, tombstoned pages and paths outside ``scope``.
    """
    if not hits:
        return hits
    meta_by_id = await _page_meta_by_id(ctx, [h["page_id"] for h in hits])

    out: list[dict] = []
    pageless = 0
    for h in hits:
        meta = _hit_meta(h, meta_by_id)
        if meta is None:
            # No page behind the id (a decision vector or store drift): nothing
            # to cite, and the later gates are keyed on a path it lacks.
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


async def _file_pageranks(ctx: Any, paths: list[str]) -> dict[str, float]:
    """PageRank of each file node among *paths*."""
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.node_id.in_(paths),
                GraphNode.node_type == "file",
            )
        )
        return {row[0]: float(row[1] or 0.0) for row in res.all()}


def _max_page_rank(hits: list[dict], pr_by_path: dict[str, float]) -> float:
    """The PageRank the bias normalises against."""
    # Over pages only, so a pageless file cannot change their bias.
    paged = {h.get("target_path") for h in hits if h.get("page_type") != PAGELESS_FILE}
    max_pr = max((pr for path, pr in pr_by_path.items() if path in paged), default=0.0)
    return max_pr or max(pr_by_path.values(), default=0.0)


async def apply_pagerank_bias(hits: list[dict], ctx: Any) -> None:
    """Multiply each hit's score by a PageRank bias, then re-sort (in place).

    Normalised within the candidates, not the whole graph, so a famous but
    unrelated file gets no extra reward.
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
        bias = 1.0 + _PAGERANK_BIAS_MAX * min(pr / max_pr, 1.0)
        h["_pagerank"] = pr
        h["_pagerank_bias"] = round(bias, 3)
        h["score"] = h.get("score", 0.0) * bias

    hits.sort(key=lambda h: h["score"], reverse=True)
