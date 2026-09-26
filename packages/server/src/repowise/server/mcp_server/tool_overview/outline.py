"""The stored wiki page tree, served shallow as the overview outline."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import Page
from repowise.server.mcp_server._budget import OmissionCollector

# The outline is the whole wiki, so it is served shallow by default: the top
# rung plus a descendant count per entry orients an agent in a few hundred
# tokens. ``include=["outline"]`` opens the next rung.
_OUTLINE_TOP_CAP = 40
_OUTLINE_CHILD_CAP = 10


async def _load_tree_rows(session: Any, repository: Any) -> list[Any]:
    """Tree columns for every live page. Tombstones are deliberately unplaced."""
    result = await session.execute(
        select(
            Page.id,
            Page.title,
            Page.page_type,
            Page.target_path,
            Page.parent_page_id,
            Page.display_order,
            Page.section_number,
        ).where(
            Page.repository_id == repository.id,
            Page.freshness_status != "tombstone",
        )
    )
    return list(result.tuples().all())


def _outline_index(rows: list[Any]) -> tuple[Any | None, dict[str, list[Any]]]:
    """Root row and parent → children map, or ``(None, {})`` for an unbuilt tree."""
    children = _children_by_parent(rows)
    if not children:
        # Every parent is null: the store predates the tree, or it has not been
        # rebuilt since. An outline built from that would be a flat list
        # dressed up as a hierarchy, so none is served.
        return None, {}
    claimed = {kid.id for kids in children.values() for kid in kids}
    candidates = [r for r in rows if r.id not in claimed and r.id in children]
    root = next(
        (r for r in candidates if r.page_type == "repo_overview"),
        candidates[0] if candidates else None,
    )
    return root, children


def _children_by_parent(rows: list[Any]) -> dict[str, list[Any]]:
    """Rows grouped under a parent that exists and is not the row itself, siblings ordered."""
    by_id = {r.id: r for r in rows}
    children: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        parent = row.parent_page_id
        if parent and parent != row.id and parent in by_id:
            children[parent].append(row)
    for siblings in children.values():
        siblings.sort(key=lambda r: (r.display_order or 0, r.target_path or "", r.id))
    return children


def _count_descendants(row: Any, children: dict[str, list[Any]], seen: set[str]) -> int:
    """Size of a subtree, guarding against a parent cycle rather than recursing into one."""
    if row.id in seen:
        return 0
    seen.add(row.id)
    return sum(1 + _count_descendants(c, children, seen) for c in children.get(row.id, []))


def _outline_node(
    row: Any, children: dict[str, list[Any]], depth: int, collector: OmissionCollector
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "section": row.section_number,
        "page_id": row.id,
        "title": row.title,
        "page_type": row.page_type,
    }
    if row.target_path:
        node["target_path"] = row.target_path
    kids = children.get(row.id, [])
    if kids:
        node["descendants"] = _count_descendants(row, children, set())
    if kids and depth > 0:
        cap = _OUTLINE_CHILD_CAP
        if len(kids) > cap:
            collector.add(
                f"outline children of {row.id} beyond cap={cap} ({len(kids) - cap} dropped)",
                "\n".join(f"{k.section_number} {k.title}: {k.target_path}" for k in kids[cap:]),
            )
        node["children"] = [_outline_node(k, children, depth - 1, collector) for k in kids[:cap]]
    return node


def _build_outline(rows: list[Any], depth: int, collector: OmissionCollector) -> dict[str, Any]:
    """The stored page tree, rooted at the repo overview.

    This is the same hierarchy the web app and the editor extension render:
    onboarding pages, then the architecture diagram, then the dependency spine
    of layers with their modules, files and cycles underneath. Each entry
    carries the dotted ``section`` it was assigned at generation time, so a
    page id quoted anywhere else in this response can be located in it.
    """
    root, children = _outline_index(rows)
    if root is None:
        return {}
    top = children.get(root.id, [])
    reachable = 1 + _count_descendants(root, children, set())
    if len(top) > _OUTLINE_TOP_CAP:
        collector.add(
            f"outline top-level entries beyond cap={_OUTLINE_TOP_CAP} "
            f"({len(top) - _OUTLINE_TOP_CAP} dropped)",
            "\n".join(
                f"{r.section_number} {r.title}: {r.target_path}" for r in top[_OUTLINE_TOP_CAP:]
            ),
        )
    outline: dict[str, Any] = {
        "root": {"page_id": root.id, "title": root.title},
        "total_pages": len(rows),
        "sections": [
            _outline_node(r, children, depth - 1, collector) for r in top[:_OUTLINE_TOP_CAP]
        ],
    }
    if len(top) > _OUTLINE_TOP_CAP:
        # Siblings are ordered by type rank, so the served entries are the
        # spine (onboarding, diagram, layers, modules) and what falls off the
        # end is the long tail of cycles and loose files. Say so rather than
        # letting the list read as the whole top rung.
        outline["sections_total"] = len(top)
        outline["sections_truncated"] = True
    # Pages the tree has no place for — a dangling parent, or a page generated
    # since the last rebuild. Reported rather than quietly missing, so the
    # section count is never read as the page count.
    unplaced = len(rows) - reachable
    if unplaced > 0:
        outline["unplaced_pages"] = unplaced
    return outline
