"""Overview, module and entry-point reads that anchor the default overview payload."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud import (
    get_kg_project_meta as _get_kg_project_meta,
)
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import filter_graph_nodes, is_excluded

# Orientation, not a directory listing — the top few modules are enough to
# point a fresh agent at the interesting subsystems. The rest are persisted
# to the omission store, not dropped.
_MODULE_CAP = 8

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
    session: Any, repository: Any, collector: OmissionCollector
) -> list[Page]:
    """Module pages capped to ``_MODULE_CAP``; the remainder goes to the omission store."""
    result = await session.execute(
        select(Page)
        .where(
            Page.repository_id == repository.id,
            Page.page_type == "module_page",
            Page.freshness_status != "tombstone",
        )
        .order_by(Page.title)
    )
    # Outline order, not alphabetical: the stored tree already ranks modules by
    # the dependency layer they sit in, so the capped-to-8 list is the top of
    # the spine rather than whatever sorts first.
    all_module_pages = sorted(result.scalars().all(), key=_module_order_key)
    if len(all_module_pages) > _MODULE_CAP:
        collector.add(
            f"module pages beyond cap={_MODULE_CAP} "
            f"({len(all_module_pages) - _MODULE_CAP} dropped)",
            "\n".join(f"{p.title}: {p.target_path}" for p in all_module_pages[_MODULE_CAP:]),
        )
    return all_module_pages[:_MODULE_CAP]


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
        return _drop_fixtures(curated_ids, exclude_spec)

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
    return [n.node_id for n in entry_nodes]


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


def _capped_entry_points(entry_ids: list[str], collector: OmissionCollector) -> list[str]:
    """First 15 entry-point ids; the remainder goes to the omission store."""
    if len(entry_ids) > 15:
        collector.add(
            f"entry points beyond cap=15 ({len(entry_ids) - 15} dropped)",
            "\n".join(entry_ids[15:]),
        )
    return entry_ids[:15]
