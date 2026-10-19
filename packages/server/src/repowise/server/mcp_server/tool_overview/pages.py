"""Overview, module and entry-point reads that anchor the default overview payload."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from repowise.core.generation.entry_points import orientation_entry_points_view
from repowise.core.persistence.crud import (
    get_kg_project_meta as _get_kg_project_meta,
)
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import filter_graph_nodes, is_excluded

# One row per package or top-level directory; the rest are persisted to the
# omission store, not dropped.
_MODULE_CAP = 12

# Split point between markdown H2 sections ("\n## ...").
_H2_SPLIT_RE = re.compile(r"\n(?=#{1,2}\s)")


def _compact_overview_content(content: str) -> str:
    """Leading section of the overview essay — the summary paragraph, not the walkthrough.

    The full essay repeats what ``key_modules`` and ``architecture.layers``
    already carry, so compact mode (the default) keeps only the first H2
    section. Callers who want the whole thing pass ``include=["content"]``.
    """
    text = (content or "").strip()
    if not text:
        return text
    return _H2_SPLIT_RE.split(text, maxsplit=1)[0].strip()


def _overview_content(overview_page: Page | None, want_full: bool) -> tuple[str, str | None]:
    """The essay served as ``content_md``, and the hint to show when it was trimmed."""
    full_content = overview_page.content if overview_page else "No overview generated yet."
    if want_full:
        return full_content, None
    content_md = _compact_overview_content(full_content)
    if content_md == full_content:
        return content_md, None
    return content_md, (
        "Overview essay trimmed to its summary section. "
        'Call get_overview(include=["content"]) for the full walkthrough.'
    )


async def _load_overview_page(session: Any, repository: Any) -> Page | None:
    """Repo overview page, preferring the canonical target_path=<repo_name> row."""
    result = await session.execute(
        select(Page)
        .where(
            Page.repository_id == repository.id,
            Page.page_type == "repo_overview",
        )
        .order_by(
            (Page.target_path == repository.name).desc(),
            Page.updated_at.desc(),
        )
    )
    return result.scalars().first()


def _section_sort_key(section: str | None) -> tuple:
    """Sort key that puts "8.10" after "8.9" instead of between "8.1" and "8.2".

    Unplaced pages (no section) sort last rather than first, so a store whose
    tree has not been rebuilt degrades to the previous title order instead of
    leading with its least-placed pages.
    """
    if not section:
        return (1,)
    try:
        return (0, tuple(int(part) for part in section.split(".")))
    except ValueError:
        return (0, ())


def _module_order_key(page: Any) -> tuple:
    """Outline position of a module page, falling back to its title."""
    return (_section_sort_key(page.section_number), page.title or "")


async def _load_module_pages(
    session: Any, repository: Any, collector: OmissionCollector, package_roots: list[str]
) -> list[dict[str, Any]]:
    """Key-module rows: the topmost module pages, capped to ``_MODULE_CAP``.

    A topmost page has no module page above it, so each package or top-level
    directory gets one row, with ``pages`` counting what sits beneath it. Too
    few rows to orient by opens the largest into its own topmost children.
    Rows follow outline order; the pages no row names go to the omission store.
    """
    result = await session.execute(
        select(Page)
        .where(
            Page.repository_id == repository.id,
            Page.page_type == "module_page",
            Page.freshness_status != "tombstone",
        )
        .order_by(Page.title)
    )
    pages = list(result.scalars().all())
    heads = _key_module_heads(pages)
    rest = [p for p in pages if p not in heads]
    if rest:
        collector.add(
            f"module pages beneath the key modules, cap={_MODULE_CAP} ({len(rest)} dropped)",
            "\n".join(f"{p.title}: {p.target_path}" for p in rest),
        )
    roots = sorted((r for r in package_roots if r), key=len, reverse=True)
    rows = []
    for head in heads:
        path = head.target_path or ""
        row = {"name": head.title, "path": head.target_path, "section": head.section_number}
        package = next((r for r in roots if _under(path, r)), path)
        if package != path:
            row["package"] = package
        below = sum(_under(p.target_path or "", path) for p in pages)
        if below > 1:
            row["pages"] = below
        rows.append(row)
    return rows


#: Fewer rows than this name too little of the repository to orient by.
_MODULE_FLOOR = 4


def _under(path: str, root: str) -> bool:
    return not root or path == root or path.startswith(root + "/")


def _topmost(pages: list[Any]) -> list[Any]:
    paths = [p.target_path or "" for p in pages]
    return [
        p
        for p in pages
        if not any(o != (p.target_path or "") and _under(p.target_path or "", o) for o in paths)
    ]


def _key_module_heads(pages: list[Any]) -> list[Any]:
    heads = _topmost(pages)
    while len(heads) < _MODULE_FLOOR:
        # Open the head with the most pages beneath it into its own top layer.
        def beneath(head: Any) -> list[Any]:
            path = head.target_path or ""
            return [
                p for p in pages if (p.target_path or "") != path and _under(p.target_path or "", path)
            ]

        widest = max(heads, key=lambda h: len(beneath(h)), default=None)
        if widest is None or not beneath(widest):
            break
        gone = widest.target_path or ""
        heads = [h for h in heads if (h.target_path or "") != gone] + _topmost(beneath(widest))
    return sorted(heads, key=_module_order_key)[:_MODULE_CAP]


def _drop_fixtures(ids: list[str], exclude_spec: Any) -> list[str]:
    """Drop excluded ids and obvious fixture/test-data paths."""
    return [
        nid
        for nid in ids
        if not is_excluded(nid, exclude_spec)
        and not any(
            seg in nid.lower() for seg in ("fixture", "test_data", "testdata", "sample_repo")
        )
    ]


async def _resolve_entry_point_ids(session: Any, repository: Any, exclude_spec: Any) -> list[str]:
    """Curated orientation entry points, falling back to the raw is_entry_point flag.

    Re-export barrels and package-export sinks are demoted; survivors are ranked
    by execution centrality. Older indexes (no kg_project_meta row) fall back to
    the flag.
    """
    proj_meta = await _get_kg_project_meta(session, repository.id)
    curated_ids: list[str] = []
    if proj_meta is not None:
        try:
            curated_ids = json.loads(proj_meta.entry_points_json or "[]")
        except (json.JSONDecodeError, TypeError):
            curated_ids = []

    if curated_ids:
        return orientation_entry_points_view(_drop_fixtures(curated_ids, exclude_spec))

    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository.id,
            GraphNode.is_entry_point == True,  # noqa: E712
            GraphNode.is_test == False,  # noqa: E712
        )
    )
    entry_nodes = filter_graph_nodes(
        [
            n
            for n in result.scalars().all()
            if not any(
                seg in n.node_id.lower()
                for seg in ("fixture", "test_data", "testdata", "sample_repo")
            )
        ],
        exclude_spec,
    )
    return orientation_entry_points_view(n.node_id for n in entry_nodes)


def _resolve_title(overview_page: Page | None, repository: Any) -> str:
    """Substitute the real repo name back into legacy "Repository Overview: repo" titles.

    Exact match only: a prefix replace would corrupt any repo whose name starts
    with "repo" ("Repository Overview: repowise" -> "...repowisewise").
    """
    if not overview_page:
        return repository.name
    persisted_title = overview_page.title or ""
    if persisted_title.strip() == "Repository Overview: repo":
        return f"Repository Overview: {repository.name}"
    return persisted_title


def _grouped_by_package(paths: list[str], package_roots: list[str]) -> list[str]:
    """*paths* with each package's entries together, packages in first-seen order."""
    roots = sorted((r for r in package_roots if r), key=len, reverse=True)
    order: dict[str, int] = {}
    for path in paths:
        order.setdefault(next((r for r in roots if _under(path, r)), ""), len(order))
    return sorted(paths, key=lambda p: order[next((r for r in roots if _under(p, r)), "")])


def _capped_entry_points(entry_ids: list[str], collector: OmissionCollector) -> list[str]:
    """First 15 entry-point ids; the remainder goes to the omission store."""
    if len(entry_ids) > 15:
        collector.add(
            f"entry points beyond cap=15 ({len(entry_ids) - 15} dropped)",
            "\n".join(entry_ids[15:]),
        )
    return entry_ids[:15]
