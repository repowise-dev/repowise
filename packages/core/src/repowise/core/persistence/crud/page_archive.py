"""Transactional retirement and readback of complete page/version records."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..batches import chunked
from ..models import Page, PageArchive, PageVersion, PageVersionArchive, _new_uuid, _now_utc

__all__ = [
    "get_retired_page",
    "get_retired_page_versions",
    "list_retired_pages",
    "purge_retired_pages",
    "retire_pages",
    "retired_page_detail",
    "retired_page_summary",
]


def _record_json(row: Page | PageVersion) -> str:
    values = {column.key: getattr(row, column.key) for column in row.__table__.columns}
    values = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in values.items()
    }
    return json.dumps(values, ensure_ascii=False)


def _carry_notes(pages: Sequence[Page], successors: Mapping[str, Page]) -> None:
    for page in pages:
        successor = successors.get(page.id)
        if successor is None or not (page.human_notes or "").strip():
            continue
        attributed = f"Notes from retired page {page.id}:\n{page.human_notes}"
        successor.human_notes = "\n\n".join(
            notes for notes in [successor.human_notes, attributed] if notes
        )


async def retire_pages(
    session: AsyncSession,
    repo_id: str,
    ids: Sequence[str],
    reason: str,
    successors: Mapping[str, str] | None = None,
) -> list[str]:
    """Archive complete records before deleting live rows in the caller's transaction.

    Retired rows never remain in ordinary page/version queries. Notes append
    only to an existing successor in the same repository, and a fresh live
    incarnation of the old id starts without the retired version history.
    """
    now = _now_utc()
    successor_map = successors or {}
    retired: list[str] = []
    for batch in chunked(sorted(set(ids))):
        pages = (
            (
                await session.execute(
                    select(Page)
                    .where(Page.repository_id == repo_id, Page.id.in_(batch))
                    .order_by(Page.id)
                )
            )
            .scalars()
            .all()
        )
        archive_ids = {page.id: _new_uuid() for page in pages}
        if not archive_ids:
            continue
        # No retired page may inherit another retired page's notes in this batch.
        targets = {
            page.id: successor_map[page.id]
            for page in pages
            if page.id in successor_map and successor_map[page.id] not in ids
        }
        target_rows = (
            (
                await session.execute(
                    select(Page).where(
                        Page.repository_id == repo_id, Page.id.in_(set(targets.values()))
                    )
                )
            )
            .scalars()
            .all()
            if targets
            else []
        )
        by_id = {target.id: target for target in target_rows}
        successors_here = {pid: by_id[target] for pid, target in targets.items() if target in by_id}
        session.add_all(
            [
                PageArchive(
                    id=archive_ids[page.id],
                    page_id=page.id,
                    repository_id=repo_id,
                    record_json=_record_json(page),
                    retired_at=now,
                    reason=reason,
                    superseded_by=successors_here[page.id].id
                    if page.id in successors_here
                    else None,
                )
                for page in pages
            ]
        )
        await session.flush()
        versions = (
            (
                await session.execute(
                    select(PageVersion).where(
                        PageVersion.repository_id == repo_id, PageVersion.page_id.in_(archive_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        session.add_all(
            [
                PageVersionArchive(
                    id=version.id,
                    archive_id=archive_ids[version.page_id],
                    record_json=_record_json(version),
                )
                for version in versions
            ]
        )
        _carry_notes(pages, successors_here)
        await session.flush()
        await session.execute(
            delete(PageVersion).where(
                PageVersion.repository_id == repo_id, PageVersion.page_id.in_(archive_ids)
            )
        )
        await session.execute(
            delete(Page).where(Page.repository_id == repo_id, Page.id.in_(archive_ids))
        )
        retired.extend(archive_ids)
    return retired


async def purge_retired_pages(
    session: AsyncSession,
    repo_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Purge only this repository's retirements strictly older than 90 days."""
    cutoff = (now or _now_utc()) - timedelta(days=90)
    expired = select(PageArchive.id).where(
        PageArchive.repository_id == repo_id, PageArchive.retired_at < cutoff
    )
    await session.execute(
        delete(PageVersionArchive).where(PageVersionArchive.archive_id.in_(expired))
    )
    result = await session.execute(
        delete(PageArchive).where(
            PageArchive.repository_id == repo_id, PageArchive.retired_at < cutoff
        )
    )
    return result.rowcount


async def list_retired_pages(
    session: AsyncSession,
    repo_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[PageArchive]:
    """List separate retirement events, newest first, within one repository."""
    result = await session.execute(
        select(PageArchive)
        .where(PageArchive.repository_id == repo_id)
        .order_by(PageArchive.retired_at.desc(), PageArchive.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars())


def retired_page_summary(page: PageArchive) -> dict:
    """The same bounded retirement identity for HTTP and CLI listings."""
    retired_at = page.retired_at
    if retired_at.tzinfo is None:
        retired_at = retired_at.replace(tzinfo=UTC)
    return {
        "archive_id": page.id,
        "page_id": page.page_id,
        "repository_id": page.repository_id,
        "retired_at": retired_at.isoformat(),
        "reason": page.reason,
        "superseded_by": page.superseded_by,
        "title": json.loads(page.record_json)["title"],
    }


def retired_page_detail(page: PageArchive, versions: Sequence[PageVersionArchive]) -> dict:
    """Complete captured records, with versions ordered newest first."""
    records = [json.loads(version.record_json) for version in versions]
    records.sort(
        key=lambda record: (record["version"], record["archived_at"], record["id"]), reverse=True
    )
    return {**retired_page_summary(page), "page": json.loads(page.record_json), "versions": records}


async def get_retired_page(
    session: AsyncSession,
    repo_id: str,
    page_id: str,
    *,
    archive_id: str | None = None,
) -> PageArchive | None:
    """Read an exact retirement, or the latest one for an old live identity."""
    query = select(PageArchive).where(
        PageArchive.repository_id == repo_id, PageArchive.page_id == page_id
    )
    if archive_id is not None:
        query = query.where(PageArchive.id == archive_id)
    result = await session.execute(
        query.order_by(PageArchive.retired_at.desc(), PageArchive.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_retired_page_versions(
    session: AsyncSession,
    repo_id: str,
    archive_id: str,
) -> list[PageVersionArchive]:
    """Read all the versions of one retirement, never of a reused live id."""
    result = await session.execute(
        select(PageVersionArchive)
        .join(PageArchive)
        .where(PageArchive.repository_id == repo_id, PageArchive.id == archive_id)
        .order_by(PageVersionArchive.id)
    )
    return list(result.scalars())
