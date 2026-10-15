"""CRUD for the ``changes`` table (Change Records)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Change, GitCommit
from ..sql import LIKE_ESCAPE, escape_like
from ._shared import _BATCH_SIZE, _batch_upsert_keyed, _row_inserter, _row_updater

_update_change = _row_updater("change_id")

#: The one JSON layout ``changes.evidence_json`` is written in. The attention
#: filter below matches text in this layout, so both read it from here.
_EVIDENCE_SEPARATORS = (", ", ": ")
_ATTENTION_LIKE = f"%{json.dumps({'status': 'attention'}, separators=_EVIDENCE_SEPARATORS)[1:-1]}%"


def dump_evidence(evidence: dict[str, Any]) -> str:
    """A change's checklist as stored in ``evidence_json``."""
    return json.dumps(evidence, separators=_EVIDENCE_SEPARATORS)


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


@dataclass(frozen=True)
class ChangeFilter:
    """Which stored changes a list reads; every field narrows, ``None`` does not.

    *since* is inclusive and *until* exclusive, on ``merged_at``. *attention*
    keeps changes whose kept checklist may flag attention (a prefilter; the
    caller confirms on the parsed evidence). *change_ids* keeps only those.
    """

    category: str | None = None
    shape: str | None = None
    q: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    attention: bool = False
    change_ids: Sequence[str] | None = None


def _filters(repository_id: str, where: ChangeFilter) -> list[Any]:
    clauses = [Change.repository_id == repository_id]
    if where.attention:
        # A kept checklist with an attention item, in dump_evidence's layout; a
        # prefilter only, since the text could sit inside an item's details,
        # and a stored status may read differently today (a kept reaching_tests
        # item is re-derived from its rows). Callers confirm each row through
        # ``collect.evidence_summary``, which parses with ``ChangeEvidence.from_dict``;
        # the re-derivation only ever lowers attention, so this stays a superset.
        clauses.append(Change.evidence_json.like(_ATTENTION_LIKE))
    if where.change_ids is not None:
        clauses.append(Change.change_id.in_(where.change_ids))
    if where.since is not None:
        clauses.append(Change.merged_at >= where.since)
    if where.until is not None:
        clauses.append(Change.merged_at < where.until)
    if where.category:
        clauses.append(Change.category == where.category)
    if where.shape:
        clauses.append(Change.shape == where.shape)
    if where.q:
        like = f"%{escape_like(where.q)}%"
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
    where: ChangeFilter | None = None,
    *,
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[Change], int]:
    """A page of changes matching *where*, newest merged first, and the total
    that matched; ``limit=None`` returns every match."""
    clauses = _filters(repository_id, where or ChangeFilter())
    total = (await session.execute(select(func.count()).select_from(Change).where(*clauses))).scalar_one()
    rows = await session.execute(
        select(Change)
        .where(*clauses)
        .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars().all()), int(total)


async def list_attention_candidates(
    session: AsyncSession, repository_id: str, where: ChangeFilter | None = None
) -> list[Any]:
    """``(change_id, evidence_json)`` rows of every change matching *where*
    whose kept checklist may flag attention, newest merged first. A
    prefilter: the caller confirms on the parsed evidence."""
    rows = await session.execute(
        select(Change.change_id, Change.evidence_json)
        .where(*_filters(repository_id, replace(where or ChangeFilter(), attention=True)))
        .order_by(Change.merged_at.desc().nulls_last(), Change.change_id)
    )
    return list(rows.all())


async def count_checklists_built(
    session: AsyncSession, repository_id: str, where: ChangeFilter | None = None
) -> tuple[int, int]:
    """``(built, changes)``: how many changes matching *where* keep a checklist."""
    built, changes = (
        await session.execute(
            select(func.count(Change.evidence_json), func.count()).where(
                *_filters(repository_id, where or ChangeFilter())
            )
        )
    ).one()
    return int(built), int(changes)


async def get_change(session: AsyncSession, repository_id: str, ref: str) -> Change | None:
    """A change by its id, or the newest change that landed a commit (a sha of 7+ hex)."""
    found = await find_changes(session, repository_id, ref, limit=1)
    return found[0] if found else None


async def find_changes(
    session: AsyncSession, repository_id: str, ref: str, *, limit: int = 2
) -> list[Change]:
    """Changes *ref* names, newest first: the one with that id, else up to
    *limit* that landed a commit starting with *ref* (7+ hex). More than one
    means a short sha is ambiguous.
    """
    row = (
        await session.execute(
            select(Change).where(Change.repository_id == repository_id, Change.change_id == ref)
        )
    ).scalar_one_or_none()
    if row is not None:
        return [row]
    if len(ref) < 7 or any(c not in "0123456789abcdef" for c in ref.lower()):
        return []
    sha = escape_like(ref.lower())
    rows = await session.execute(
        select(Change)
        .where(
            Change.repository_id == repository_id,
            or_(
                Change.head_sha.like(f"{sha}%", escape=LIKE_ESCAPE),
                Change.commit_shas_json.like(f'%"{sha}%', escape=LIKE_ESCAPE),
            ),
        )
        .order_by(Change.merged_at.desc().nulls_last())
        .limit(limit)
    )
    return list(rows.scalars().all())


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
