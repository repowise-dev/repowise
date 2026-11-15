"""What a tombstoned page tells a reader, and which page a file path serves.

A tombstone is written for two different reasons, and only one of them is
news about the file:

* **deleted** — the file was deleted or renamed. The page tells a reader the
  file is gone, and ``successor_paths`` names where a rename went.
* **retired** — the build stopped making this page: a file page superseded by
  an API contract page, a regrouped module, a deselected spotlight, a page
  outside the indexed scope. The source may be live, and a reader is served
  whatever page covers it now.

Every reader asks this module, so the rule lives in one place. A tombstone
written before the reason was recorded reads as ``deleted``, which is what
every reader assumed then; a live page for the same file still wins over it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Page

TOMBSTONE = "tombstone"

# Why a page became a tombstone, in its metadata.
TOMBSTONE_REASON_KEY = "tombstone_reason"
TOMBSTONE_DELETED = "deleted"
TOMBSTONE_RETIRED = "retired"


def tombstone_reason(metadata_json: str | None) -> str:
    """The recorded reason; ``deleted`` for a row written before reasons were."""
    try:
        meta = json.loads(metadata_json or "{}")
    except (ValueError, TypeError):
        meta = {}
    reason = meta.get(TOMBSTONE_REASON_KEY) if isinstance(meta, dict) else None
    return TOMBSTONE_RETIRED if reason == TOMBSTONE_RETIRED else TOMBSTONE_DELETED


def announces_deletion(page: Any) -> bool:
    """Whether *page* is a tombstone that says its file was deleted or renamed."""
    return (
        getattr(page, "freshness_status", None) == TOMBSTONE
        and tombstone_reason(getattr(page, "metadata_json", None)) == TOMBSTONE_DELETED
    )


def _reader_page(pages: Iterable[Any]) -> Any | None:
    """Of one file's pages, the one a reader gets.

    A live page wins, the file page first. Failing that, the tombstone that
    announces the file's deletion. A retired tombstone is never served.
    """
    candidates = sorted(pages, key=lambda p: (p.page_type != "file_page", p.page_type))
    live = [p for p in candidates if p.freshness_status != TOMBSTONE]
    if live:
        return live[0]
    return next((p for p in candidates if announces_deletion(p)), None)


def _file_backed_types() -> frozenset[str]:
    from repowise.core.generation.interlinking import FILE_BACKED_PAGE_TYPES

    return FILE_BACKED_PAGE_TYPES


async def file_backed_page(session: AsyncSession, repo_id: str, path: str) -> Page | None:
    """The page that documents file *path*, or ``None``.

    Its live file, API contract or infra page; else a tombstone saying the
    file is gone. A router whose file page was superseded by its API contract
    page gets the contract page, not a deletion notice.
    """
    res = await session.execute(
        select(Page).where(
            Page.repository_id == repo_id,
            Page.target_path == path,
            Page.page_type.in_(_file_backed_types()),
        )
    )
    return _reader_page(res.scalars().all())


async def retired_page_successor(session: AsyncSession, page: Page) -> Page | None:
    """The live page that documents a retired tombstone's file, or ``None``.

    A superseded file page leads to its API contract page, a deselected
    spotlight to its file's page. A page that names no file, such as a
    regrouped module, has no successor here.
    """
    if page.freshness_status != TOMBSTONE or announces_deletion(page):
        return None
    if page.page_type != "symbol_spotlight" and page.page_type not in _file_backed_types():
        return None
    path = (page.target_path or "").split("::", 1)[0]
    successor = await file_backed_page(session, page.repository_id, path)
    if successor is None or successor.freshness_status == TOMBSTONE:
        return None
    return successor


async def deleted_file_paths(session: AsyncSession, repo_id: str, paths: Iterable[str]) -> set[str]:
    """Those of *paths* whose page says the file was deleted or renamed."""
    wanted = list(set(paths))
    if not wanted:
        return set()
    res = await session.execute(
        select(Page.target_path, Page.page_type, Page.freshness_status, Page.metadata_json).where(
            Page.repository_id == repo_id,
            Page.target_path.in_(wanted),
            Page.page_type.in_(_file_backed_types()),
        )
    )
    by_path: dict[str, list[Any]] = {}
    for row in res.all():
        by_path.setdefault(row.target_path, []).append(row)
    return {
        path
        for path, rows in by_path.items()
        if (page := _reader_page(rows)) is not None and page.freshness_status == TOMBSTONE
    }
