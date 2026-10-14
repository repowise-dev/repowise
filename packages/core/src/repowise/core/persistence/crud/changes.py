"""CRUD for the ``changes`` table (Change Records)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Change, GitCommit
from ..sql import LIKE_ESCAPE, escape_like
from ._shared import _BATCH_SIZE, _batch_upsert_keyed, _row_inserter, _row_updater

_update_change = _row_updater("change_id")


async def upsert_changes(session: AsyncSession, repository_id: str, rows: list[dict]) -> None:
    """Bulk upsert change rows keyed on ``(repository_id, change_id)``."""
    await _batch_upsert_keyed(
        session,
        Change,
        rows,
        prefilter=(Change.repository_id == repository_id,),
        item_key_fn=lambda row: row.get("change_id", ""),
        row_key_fn=lambda row: row.change_id,
        update_fn=_update_change,
        insert_fn=_row_inserter(Change, repository_id),
        batch_size=_BATCH_SIZE,
    )


async def delete_changes(session: AsyncSession, repository_id: str) -> None:
    await session.execute(delete(Change).where(Change.repository_id == repository_id))
    await session.flush()


async def get_change_heads(session: AsyncSession, repository_id: str) -> dict[str, str]:
    """``change_id -> head_sha`` for every stored change."""
    rows = await session.execute(
        select(Change.change_id, Change.head_sha).where(Change.repository_id == repository_id)
    )
    return dict(rows.tuples().all())


async def get_newest_change_head(session: AsyncSession, repository_id: str) -> str | None:
    """Head sha of the most recently merged stored change."""
    return (
        await session.execute(
            select(Change.head_sha)
            .where(Change.repository_id == repository_id)
            .order_by(Change.merged_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


def _filters(repository_id: str, category: str | None, shape: str | None, q: str | None):
    clauses = [Change.repository_id == repository_id]
    if category:
        clauses.append(Change.category == category)
    if shape:
        clauses.append(Change.shape == shape)
    if q:
        like = f"%{escape_like(q)}%"
        clauses.append(
            or_(
                Change.title.ilike(like, escape=LIKE_ESCAPE),
                Change.change_id.ilike(like, escape=LIKE_ESCAPE),
                Change.files_json.ilike(like, escape=LIKE_ESCAPE),
            )
        )
    return clauses


async def list_changes(
    session: AsyncSession,
    repository_id: str,
    *,
    category: str | None = None,
    shape: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Change], int]:
    """A page of changes, newest merged first, and the total that matched."""
    clauses = _filters(repository_id, category, shape, q)
    total = (await session.execute(select(func.count()).select_from(Change).where(*clauses))).scalar_one()
    rows = await session.execute(
        select(Change)
        .where(*clauses)
        .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars().all()), int(total)


async def get_change(session: AsyncSession, repository_id: str, ref: str) -> Change | None:
    """A change by its id, or the change that landed a commit (a sha of 7+ hex)."""
    row = (
        await session.execute(
            select(Change).where(Change.repository_id == repository_id, Change.change_id == ref)
        )
    ).scalar_one_or_none()
    if row is not None or len(ref) < 7 or any(c not in "0123456789abcdef" for c in ref.lower()):
        return row
    sha = escape_like(ref.lower())
    return (
        await session.execute(
            select(Change)
            .where(
                Change.repository_id == repository_id,
                or_(
                    Change.head_sha.like(f"{sha}%", escape=LIKE_ESCAPE),
                    Change.commit_shas_json.like(f'%"{sha}%', escape=LIKE_ESCAPE),
                ),
            )
            .order_by(Change.merged_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_commit_labels(
    session: AsyncSession, repository_id: str, shas: Sequence[str]
) -> dict[str, tuple[str | None, str | None]]:
    """``sha -> (attribution, agent_name)`` for the stored commits among *shas*."""
    out: dict[str, tuple[str | None, str | None]] = {}
    wanted = list(dict.fromkeys(shas))
    for i in range(0, len(wanted), 500):
        rows = await session.execute(
            select(GitCommit.sha, GitCommit.attribution, GitCommit.agent_name).where(
                GitCommit.repository_id == repository_id, GitCommit.sha.in_(wanted[i : i + 500])
            )
        )
        for sha, attribution, agent in rows.tuples():
            out[sha] = (attribution, agent)
    return out


async def get_category_inputs(
    session: AsyncSession, repository_id: str, limit: int = 1000
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str, str]]]:
    """``(sha, subject, category)`` per commit and ``(change_id, title, category)`` per change.

    Newest *limit* of each, so the read stays bounded as the tables grow.
    """
    commits = await session.execute(
        select(GitCommit.sha, GitCommit.subject, GitCommit.category)
        .where(GitCommit.repository_id == repository_id)
        .order_by(GitCommit.committed_at.desc().nulls_last())
        .limit(limit)
    )
    changes = await session.execute(
        select(Change.change_id, Change.title, Change.category)
        .where(Change.repository_id == repository_id)
        .order_by(Change.merged_at.desc().nulls_last())
        .limit(limit)
    )
    return list(commits.tuples().all()), list(changes.tuples().all())
