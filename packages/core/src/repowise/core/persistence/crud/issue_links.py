"""CRUD for tracker issue links (``issue_links``), see :mod:`repowise.core.issues`.

:func:`store_issue_links` is the one writer, for a ``repowise sources sync``
fetch and for a host that fetched links itself and hands them over: the index
never fetches. The readers give the fix walk its commit set and the fix
surfaces the issue each fix links to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ...git_sha import is_full_sha, is_sha_prefix
from ...issues.sources import DEFECT_TYPES, StoredLink
from ..models import FixEvent, IssueLink, _now_utc
from ._shared import _BATCH_SIZE

if TYPE_CHECKING:
    from ...ingestion.git_indexer.prior_defects import TrackerFixes

__all__ = [
    "LinkedIssue",
    "change_issue_quotes",
    "cite_prior_defect_issues",
    "commit_issues",
    "decision_issues",
    "fix_issue_quotes",
    "linked_defect_issues",
    "load_tracker_fixes",
    "quoted_keys",
    "record_linked_issues",
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


async def load_tracker_fixes(session: AsyncSession, repository_id: str) -> TrackerFixes:
    """Every commit a stored bug or incident issue links to, with those issues' keys.

    The fix walk's tracker fixes: a commit here is a fix, and the keys let the
    walk count one issue once per file (see ``prior_defects.issue_repeats``).
    """
    result = await session.execute(
        select(IssueLink.commit_sha, IssueLink.issue_key).where(
            IssueLink.repository_id == repository_id,
            IssueLink.issue_type.in_(sorted(DEFECT_TYPES)),
        )
    )
    keys: dict[str, set[str]] = {}
    for sha, key in result.all():
        keys.setdefault(sha, set()).add(key)
    return {sha: frozenset(found) for sha, found in keys.items()}


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


def quoted_keys(quotes: Iterable[str]) -> list[str]:
    """The issue keys of :func:`fix_issue_quotes` quotes: ``ENG-42 (bug): ...`` -> ``ENG-42``."""
    return [quote.split(" (", 1)[0] for quote in quotes]


async def cite_prior_defect_issues(
    session: AsyncSession, repository_id: str, rows: Iterable[dict]
) -> None:
    """Add ``details.linked_issues`` to each ``prior_defect`` finding row a linked issue backs.

    In place, read when a finding is shown, so a link synced after the
    finding was scored still shows. A row on a file no stored bug or incident
    reaches is left as it was.
    """
    wanted = [
        row
        for row in rows
        if row.get("biomarker_type") == "prior_defect"
        and isinstance(row.get("details"), dict)
        and row.get("file_path")
    ]
    if not wanted:
        return
    quotes = await fix_issue_quotes(session, repository_id, {row["file_path"] for row in wanted})
    for row in wanted:
        if found := quotes.get(row["file_path"]):
            row["details"] = {**row["details"], "linked_issues": found}


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


def _issue(key: str, kind: str, status: str, title: str, url: str) -> dict[str, str]:
    """One linked issue as surfaces show it; empty fields left out."""
    out = {"key": key, "type": kind, "status": status, "title": title, "url": url}
    return {k: v for k, v in out.items() if v}


def _issue_quote(issue: dict[str, str]) -> str:
    """``ENG-42 (bug, Done): Checkout fails``: a stored issue in one string."""
    facts = ", ".join(v for v in (issue.get("type"), issue.get("status")) if v)
    head = f"{issue['key']} ({facts})" if facts else issue["key"]
    title = issue.get("title", "")
    if not title:
        return head
    return f"{head}: {title if len(title) <= 80 else title[:77] + '...'}"


_ISSUE_COLUMNS = (
    IssueLink.commit_sha,
    IssueLink.issue_key,
    IssueLink.issue_type,
    IssueLink.status,
    IssueLink.title,
    IssueLink.url,
)


async def commit_issues(
    session: AsyncSession, repository_id: str, commits: Iterable[str]
) -> dict[str, list[dict[str, str]]]:
    """``commit -> [{key, type, status, title, url}]`` for the stored links on *commits*.

    A commit is a full sha or a prefix of 7 or more hex characters, as a
    decision's evidence commits hold them; the result is keyed by the value
    given. Empty for an index that predates the table.
    """
    wanted = {c.strip().lower() for c in commits if isinstance(c, str)}
    wanted = {c for c in wanted if is_full_sha(c) or is_sha_prefix(c)}
    if not wanted:
        return {}
    try:
        async with session.begin_nested():
            rows = await _rows_on_prefixes(session, repository_id, sorted(wanted))
    except SQLAlchemyError:
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for sha, key, kind, status, title, url in rows:
        for commit in wanted:
            if sha.startswith(commit):
                out.setdefault(commit, []).append(_issue(key, kind, status, title, url))
    return out


async def decision_issues(
    session: AsyncSession, repository_id: str, commits_by_decision: dict[str, list]
) -> dict[str, list[dict[str, str]]]:
    """``decision id -> [issue]``: the stored issues its evidence commits link to.

    Each issue once, in the order its commits are listed. Display only: what
    a decision governs, its authority and its ranking never read this.
    """
    shas = {
        d: [_sha_of(c) for c in commits] if isinstance(commits, list) else []
        for d, commits in commits_by_decision.items()
    }
    flat = [c for commits in shas.values() for c in commits]
    by_commit = await commit_issues(session, repository_id, flat)
    if not by_commit:
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for decision_id, commits in shas.items():
        issues: dict[str, dict[str, str]] = {}
        for commit in commits:
            for issue in by_commit.get(commit, ()):
                issues.setdefault(issue["key"], issue)
        if issues:
            out[decision_id] = list(issues.values())
    return out


def _sha_of(value: object) -> str:
    """An evidence commit as stored: a sha string, or a row naming one."""
    if isinstance(value, dict):
        value = value.get("commit") or value.get("sha") or ""
    return value.strip().lower() if isinstance(value, str) else ""


async def _rows_on_prefixes(
    session: AsyncSession, repository_id: str, prefixes: list[str]
) -> list[tuple]:
    # A range per prefix keeps the primary key in use: hex sorts below "g".
    rows: list[tuple] = []
    for chunk in _chunks(prefixes):
        result = await session.execute(
            select(*_ISSUE_COLUMNS)
            .where(
                IssueLink.repository_id == repository_id,
                or_(
                    *(
                        and_(IssueLink.commit_sha >= p, IssueLink.commit_sha < p + "g")
                        for p in chunk
                    )
                ),
            )
            .order_by(IssueLink.commit_sha, IssueLink.issue_key)
        )
        rows.extend(result.all())
    return rows


async def change_issue_quotes(
    session: AsyncSession,
    repository_id: str,
    keys: Sequence[str] | None,
    shas: Iterable[str],
    *,
    limit: int = 5,
) -> list[str] | None:
    """A change's linked issues with what the tracker says about each.

    *keys* are the bare keys the change metadata holds, kept in order; each
    becomes ``key (type, status): title`` when a stored link names it, the
    newest fetch winning. Issues stored on the change's *shas* that *keys*
    does not hold follow, at most *limit* of them. ``None`` when no stored
    link touches the change, so a caller keeps its bare keys as they were.
    """
    listed = list(keys or [])
    change_shas = {s for s in shas if s}
    try:
        async with session.begin_nested():
            rows = await _change_rows(session, repository_id, listed, sorted(change_shas))
    except SQLAlchemyError:
        return None
    if not rows:
        return None
    on_change: dict[str, dict[str, str]] = {}
    anywhere: dict[str, dict[str, str]] = {}
    for sha, key, kind, status, title, url in rows:
        target = on_change if sha in change_shas else anywhere
        target.setdefault(key, _issue(key, kind, status, title, url))
    found = {**anywhere, **on_change}
    out = [_issue_quote(found[k]) if k in found else k for k in listed]
    seen = set(listed)
    extra = [k for k in on_change if k not in seen]
    return out + [_issue_quote(on_change[k]) for k in extra[:limit]]


async def record_linked_issues(
    session: AsyncSession, repository_id: str, record: dict
) -> list[str] | None:
    """A Change Record's ``metadata.linked_issues`` with the stored tracker facts.

    The bare keys as the record holds them when no stored link touches it.
    A fix that a linked bug made one gets that issue as its ``category_basis``,
    in place, since the row alone cannot name it.
    """
    keys = record["metadata"]["linked_issues"]
    shas = [record["head_sha"], *record["commits"]]
    quoted = await change_issue_quotes(session, repository_id, keys, shas)
    if quoted is not None and record.get("category") == "fix" and not record.get("category_basis"):
        await _issue_basis(session, repository_id, record, shas)
    return keys if quoted is None else quoted


async def _issue_basis(
    session: AsyncSession, repository_id: str, record: dict, shas: list[str]
) -> None:
    """The linked bug as the record's ``category_basis``, chosen as ``changes.store.linked_bug`` does."""
    from ...changes.category import SIGNAL_ISSUE

    try:
        linked = await linked_defect_issues(session, repository_id, shas)
    except SQLAlchemyError:
        return
    for sha in shas:
        if linked.get(sha):
            bug = min(issue.key for issue in linked[sha])
            record["category_basis"] = {"signal": SIGNAL_ISSUE, "value": bug}
            return


async def _change_rows(
    session: AsyncSession, repository_id: str, keys: list[str], shas: list[str]
) -> list[tuple]:
    """Stored links on *shas* or naming *keys*, newest fetch first."""
    rows: list[tuple] = []
    for column, values in ((IssueLink.commit_sha, shas), (IssueLink.issue_key, keys)):
        for chunk in _chunks(values):
            result = await session.execute(
                select(*_ISSUE_COLUMNS)
                .where(IssueLink.repository_id == repository_id, column.in_(chunk))
                .order_by(IssueLink.fetched_at.desc(), IssueLink.commit_sha, IssueLink.issue_key)
            )
            rows.extend(result.all())
    return rows


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
