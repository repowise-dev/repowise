"""Rebuild the stored page tree from the complete page set.

Placement is a property of the whole wiki, not of one page: which module a
file sits under depends on which module pages exist, and a page's order
depends on its siblings. An incremental update only holds the handful of
pages it regenerated, so computing the tree from that set alone would answer
with whatever happened to be in memory, and each update would flatten a little
more of the tree.

So the tree is rebuilt from the store after persisting, where the whole set
lives. That makes added, deleted and renamed files fall out for free: the row
set is simply what it is at that moment, and the tree describes it.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def rebuild_page_tree(session: Any, repo_id: str) -> int:
    """Recompute and store parent, order and section for every page of a repo.

    Returns the number of rows whose placement changed. Safe to call after any
    write to wiki_pages, and cheap enough to call unconditionally: it reads
    four small columns plus metadata and writes only what moved.
    """
    from sqlalchemy import select

    from repowise.core.generation.page_tree import TreeNode, assign_page_tree
    from repowise.core.persistence.models import Page

    rows = (
        (
            await session.execute(
                select(
                    Page.id,
                    Page.page_type,
                    Page.target_path,
                    Page.metadata_json,
                    Page.parent_page_id,
                    Page.display_order,
                    Page.section_number,
                    Page.freshness_status,
                ).where(Page.repository_id == repo_id)
            )
        )
        .tuples()
        .all()
    )
    if not rows:
        return 0

    nodes: list[TreeNode] = []
    before: dict[str, tuple[str | None, int, str | None]] = {}
    layer_order_ids: list[str] = []

    for pid, page_type, target, meta_json, parent, order, section, freshness in rows:
        # A tombstoned page documents a file that no longer exists. Leaving it
        # in the tree would show deleted files in the navigation, so it keeps
        # its row and its content but loses its place.
        if freshness == "tombstone":
            before[pid] = (parent, order, section)
            continue
        try:
            metadata = json.loads(meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        if page_type == "repo_overview" and not layer_order_ids:
            spine = metadata.get("layer_order_ids")
            if isinstance(spine, list):
                layer_order_ids = [s for s in spine if isinstance(s, str)]
        before[pid] = (parent, order, section)
        nodes.append(
            TreeNode(
                page_id=pid,
                page_type=page_type or "",
                target_path=target or "",
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )

    assign_page_tree(nodes, layer_order_ids)

    placed = {n.page_id: (n.parent_page_id, n.display_order, n.section_number) for n in nodes}
    changed = 0
    for pid, current in before.items():
        # A tombstoned page is not in ``placed``; unplace it rather than
        # leaving whatever it carried from when its file still existed.
        wanted = placed.get(pid, (None, 0, None))
        if wanted != current:
            changed += 1

    if not changed:
        return 0

    for page in (
        (await session.execute(select(Page).where(Page.repository_id == repo_id))).scalars().all()
    ):
        parent, order, section = placed.get(page.id, (None, 0, None))
        page.parent_page_id = parent
        page.display_order = order
        page.section_number = section

    logger.info("page_tree_rebuilt", repo_id=repo_id, pages=len(nodes), changed=changed)
    return changed
