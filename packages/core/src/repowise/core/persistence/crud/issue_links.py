"""CRUD for tracker issue links (``issue_links``), see :mod:`repowise.core.issues`.

:func:`store_issue_links` is the one writer, for a ``repowise sources sync``
fetch and for a host that fetched links itself and hands them over: the index
never fetches. The readers give the fix walk its commit set and the fix
surfaces the issue each fix links to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ...issues.sources import DEFECT_TYPES, StoredLink
from ..models import FixEvent, IssueLink, _now_utc
from ._shared import _BATCH_SIZE

__all__ = [
    "LinkedIssue",
    "fix_issue_quotes",
    "linked_defect_issues",
    "load_tracker_fixes",
    "store_issue_links",
]


async def store_issue_links(
    session: AsyncSession,
    repository_id: str,
    links: Sequence[StoredLink],
    *,
    source: str,
    covered_shas: Iterable[str] | None = None,
) -> dict:
    """Store *links* from *source*; returns ``{stored, removed, defect_changed}``.

    All or nothing: the writes run in a savepoint, so a failure leaves the
    stored links as they were. A (commit, issue) pair already stored is
    replaced, whichever source stored it. *covered_shas* names the
    commits a complete fetch asked about: this source's links on them that
    *links* no longer holds are removed. Leave it None for a partial fetch,
    which only adds. ``defect_changed`` lists the commits whose fix status
    moved (a bug or incident link came or went), the ones an index already
    holding them must re-walk.
    """
    async with session.begin_nested():
        return await _store(session, repository_id, links, source, covered_shas)


async def _store(
    session: AsyncSession,
    repository_id: str,
    links: Sequence[StoredLink],
    source: str,
    covered_shas: Iterable[str] | None,
) -> dict:
    rows: dict[tuple[str, str], StoredLink] = {}
    for link in links:
        rows[(link.sha, link.key)] = link
    covered = set(covered_shas) if covered_shas is not None else set()
    touched = covered | {sha for sha, _ in rows}
    before = await _defect_shas(session, repository_id, touched)

    stale: set[tuple[str, str]] = set()
    for chunk in _chunks(sorted(covered)):
        result = await session.execute(
            select(IssueLink.commit_sha, IssueLink.issue_key).where(
                IssueLink.repository_id == repository_id,
                IssueLink.source == source,
                IssueLink.commit_sha.in_(chunk),
            )
        )
        stale.update((sha, key) for sha, key in result.all())
    stale -= set(rows)
    for chunk in _chunks(sorted(stale | set(rows))):
        await session.execute(
            delete(IssueLink).where(
                IssueLink.repository_id == repository_id,
                or_(*(and_(IssueLink.commit_sha == s, IssueLink.issue_key == k) for s, k in chunk)),
            )
        )
    stamp = _now_utc()
    values = [
        {
            "repository_id": repository_id,
            "commit_sha": link.sha,
            "issue_key": link.key,
            "source": source,
            "issue_type": link.type,
            "status": link.status,
            "title": link.title,
            "url": link.url,
            "basis": link.basis,
            "fetched_at": stamp,
        }
        for link in rows.values()
    ]
    for chunk in _chunks(values):
        await session.execute(insert(IssueLink), chunk)
    await session.flush()

    after = await _defect_shas(session, repository_id, touched)
    return {
        "stored": len(values),
        "removed": len(stale),
        "defect_changed": sorted(before ^ after),
    }


async def load_tracker_fixes(session: AsyncSession, repository_id: str) -> frozenset[str]:
    """Every commit a stored bug or incident issue links to: the fix walk's tracker fixes."""
    result = await session.execute(
        select(IssueLink.commit_sha)
        .where(
            IssueLink.repository_id == repository_id,
            IssueLink.issue_type.in_(sorted(DEFECT_TYPES)),
        )
        .distinct()
    )
    return frozenset(result.scalars().all())


@dataclass(frozen=True)
class LinkedIssue:
    """A bug or incident issue a fix commit links to, as fix surfaces quote it."""

    key: str
    type: str
    title: str

    def quote(self) -> str:
        """``ENG-42 (bug): Checkout fails on an empty cart``, short enough to inline."""
        head = f"{self.key} ({self.type})"
        if not self.title:
            return head
        title = self.title if len(self.title) <= 80 else self.title[:77] + "..."
        return f"{head}: {title}"


async def linked_defect_issues(
    session: AsyncSession, repository_id: str, shas: Iterable[str]
) -> dict[str, list[LinkedIssue]]:
    """``sha -> [LinkedIssue]`` for the commits among *shas* a bug or incident links to."""
    out: dict[str, list[LinkedIssue]] = {}
    for chunk in _chunks(sorted(set(shas))):
        result = await session.execute(
            select(IssueLink.commit_sha, IssueLink.issue_key, IssueLink.issue_type, IssueLink.title)
            .where(
                IssueLink.repository_id == repository_id,
                IssueLink.commit_sha.in_(chunk),
                IssueLink.issue_type.in_(sorted(DEFECT_TYPES)),
            )
            .order_by(IssueLink.commit_sha, IssueLink.issue_key)
        )
        for sha, key, kind, title in result.all():
            out.setdefault(sha, []).append(LinkedIssue(key, kind, title or ""))
    return out


async def fix_issue_quotes(
    session: AsyncSession, repository_id: str, paths: Iterable[str], *, limit: int = 3
) -> dict[str, list[str]]:
    """``path -> ["ENG-42 (bug): title", ...]``: the issues behind each file's counted fixes.

    Newest fix first, each issue once, at most *limit* per file. Empty for a
    file whose fixes no stored bug or incident links to, and for an index
    that predates the table, so a fix surface adds nothing it cannot back.
    """
    wanted = sorted(set(paths))
    if not wanted:
        return {}
    try:
        # A savepoint, so a failed read (a store older than the table) leaves
        # the caller's transaction usable on Postgres.
        async with session.begin_nested():
            events, linked = await _quoted_events(session, repository_id, wanted)
    except SQLAlchemyError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for path, sha in events:
        quotes = out.setdefault(path, {})
        for issue in linked.get(sha, ()):
            if len(quotes) < limit:
                quotes.setdefault(issue.key, issue.quote())
    return {path: list(q.values()) for path, q in out.items() if q}


async def _quoted_events(
    session: AsyncSession, repository_id: str, wanted: list[str]
) -> tuple[list[tuple[str, str]], dict[str, list[LinkedIssue]]]:
    """Each file's counted fix events, newest first, and the defect issues they link to."""
    events: list[tuple[str, str]] = []
    for chunk in _chunks(wanted):
        result = await session.execute(
            select(FixEvent.file_path, FixEvent.fix_sha)
            .where(
                FixEvent.repository_id == repository_id,
                FixEvent.file_path.in_(chunk),
                FixEvent.shape_kind == "code_fix",
            )
            .order_by(FixEvent.committed_at.desc())
        )
        events.extend(result.all())
    linked = await linked_defect_issues(session, repository_id, (sha for _, sha in events))
    return events, linked


async def _defect_shas(session: AsyncSession, repository_id: str, shas: set[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(sorted(shas)):
        result = await session.execute(
            select(IssueLink.commit_sha).where(
                IssueLink.repository_id == repository_id,
                IssueLink.commit_sha.in_(chunk),
                IssueLink.issue_type.in_(sorted(DEFECT_TYPES)),
            )
        )
        found.update(result.scalars().all())
    return found


def _chunks(items: list) -> Iterable[list]:
    # Under SQLite's bound-parameter limit, with room for the other parameters.
    step = min(_BATCH_SIZE, 400)
    for i in range(0, len(items), step):
        yield items[i : i + step]
