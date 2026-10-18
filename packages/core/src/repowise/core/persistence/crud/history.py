"""Reads behind the better/worse history surfaces: commit filters, stored
health deltas in bulk, hotspot touches, and what landed in a window.

Every bulk read takes a page's shas and answers in one query per 500, so a
listing never issues a query per row.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...analysis.history.delta import DeltaStamp
from ..models import (
    GitCommit,
    GitCommitFile,
    GitCommitHealthDelta,
    GitCommitHealthFinding,
    GitMetadata,
    HealthSnapshot,
    RefactoringPayoff,
    RefactoringSuggestion,
)
from ..sql import LIKE_ESCAPE, escape_like

_CHUNK = 500


@dataclass(frozen=True)
class CommitFilter:
    """Which commits a history read keeps; every field narrows, ``None`` does not.

    *author* matches the author email (any case) or the exact author name.
    *area* is a directory (``packages/core``) or one file path. *since* is
    inclusive and *until* exclusive, on ``committed_at``. *delta* is one of
    :data:`repowise.core.analysis.history.DELTA_FILTERS`; it keeps only scanned
    commits. *touches_hotspot* keeps commits that touched a hotspot file.
    *authorship* and *kind* are the feed's attribution and review-priority
    filters; *high_cut* is the repo's high-band boundary that *kind* ``high``
    compares against. *stamp* is the current analyzer's; *delta* reads only
    rows written under it.
    """

    authorship: str | None = None
    kind: str | None = None
    high_cut: float | None = None
    author: str | None = None
    area: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    delta: str | None = None
    touches_hotspot: bool = False
    stamp: DeltaStamp | None = None


def _commit_authorship_clause(authorship: str | None):
    """Optional authorship predicate over the three-state label: ``agent``,
    ``human`` (human-attested only; a commit without a marker is never human)
    or ``unknown``. ``None`` / ``"all"`` means no filtering."""
    if authorship == "agent":
        return GitCommit.agent_name.is_not(None)
    if authorship == "human":
        return GitCommit.attribution == "human_attested"
    if authorship == "unknown":
        return and_(
            GitCommit.agent_name.is_(None),
            or_(GitCommit.attribution.is_(None), GitCommit.attribution == "unknown"),
        )
    return None


def _commit_kind_clause(kind: str | None, high_cut: float | None):
    """Optional predicate for the review-priority band or bug-fix commits.

    ``high`` compares against the repo's own moderate/high boundary on the
    score axis, which is where the tercile actually falls; deriving it per row
    would mean ranking every commit before paging any of them.
    """
    if kind == "fixes":
        return GitCommit.is_fix.is_(True)
    if kind == "high":
        if high_cut is None:
            return None
        return GitCommit.change_risk_score >= high_cut
    return None


def author_clause(name_column: Any, email_column: Any, author: str) -> Any:
    """The author email (any case) or exact name is *author*."""
    return or_(func.lower(email_column) == author.strip().lower(), name_column == author.strip())


def area_clause(column: Any, area: str) -> Any:
    """*column* is the path *area* names, or a path under it."""
    root = area.strip().strip("/")
    return or_(column == root, column.like(f"{escape_like(root)}/%", escape=LIKE_ESCAPE))


def current_delta_clause(stamp: DeltaStamp | None) -> Any:
    """``GitCommitHealthDelta`` rows written under *stamp*, the current analyzer.

    Every aggregate and filter reads only these: counts from two analyzer
    versions never add up. A filter without a stamp is a caller bug.
    """
    if stamp is None:
        raise ValueError("a health delta filter needs the current analyzer stamp")
    return and_(
        GitCommitHealthDelta.analyzer_version == stamp.analyzer_version,
        GitCommitHealthDelta.rules_fingerprint == stamp.rules_fingerprint,
        GitCommitHealthDelta.performance_model_version == stamp.performance_model_version,
    )


def _delta_clause(wanted: str, stamp: DeltaStamp | None) -> Any:
    """SQL twin of :func:`repowise.core.analysis.history.matches_delta`, over
    current-stamp rows only.

    Better is resolved plus improved, exactly as
    :func:`~repowise.core.analysis.history.summarize_delta` reads a current
    row; ``test_history_routes`` holds the two to the same answer.
    """
    better = (GitCommitHealthDelta.resolved_count + GitCommitHealthDelta.improved_count) > 0
    worse = (GitCommitHealthDelta.introduced_count + GitCommitHealthDelta.worsened_count) > 0
    condition = {
        "improved": better,
        "worsened": worse,
        "mixed": and_(better, worse),
        "neutral": and_(~better, ~worse),
    }[wanted]
    return exists().where(
        GitCommitHealthDelta.repository_id == GitCommit.repository_id,
        GitCommitHealthDelta.sha == GitCommit.sha,
        current_delta_clause(stamp),
        condition,
    )


def _touches_hotspot_clause() -> Any:
    return exists().where(
        GitCommitFile.repository_id == GitCommit.repository_id,
        GitCommitFile.sha == GitCommit.sha,
        GitMetadata.repository_id == GitCommitFile.repository_id,
        GitMetadata.file_path == GitCommitFile.file_path,
        GitMetadata.is_hotspot.is_(True),
    )


def _commit_area_clause(area: str | None) -> Any:
    if not area or not area.strip().strip("/"):
        return None
    return exists().where(
        GitCommitFile.repository_id == GitCommit.repository_id,
        GitCommitFile.sha == GitCommit.sha,
        area_clause(GitCommitFile.file_path, area),
    )


def _window_clauses(since: datetime | None, until: datetime | None) -> list[Any]:
    return [
        *([GitCommit.committed_at >= since] if since is not None else []),
        *([GitCommit.committed_at < until] if until is not None else []),
    ]


def commit_filter_clauses(where: CommitFilter | None) -> list[Any]:
    """The ``GitCommit`` predicates *where* asks for (repository scoping is the caller's)."""
    if where is None:
        return []
    optional = [
        _commit_authorship_clause(where.authorship),
        _commit_kind_clause(where.kind, where.high_cut),
        author_clause(GitCommit.author_name, GitCommit.author_email, where.author)
        if where.author
        else None,
        _commit_area_clause(where.area),
        _delta_clause(where.delta, where.stamp) if where.delta else None,
        _touches_hotspot_clause() if where.touches_hotspot else None,
    ]
    return [c for c in optional if c is not None] + _window_clauses(where.since, where.until)


def _chunks(shas: Sequence[str]) -> list[list[str]]:
    wanted = list(dict.fromkeys(shas))
    return [wanted[i : i + _CHUNK] for i in range(0, len(wanted), _CHUNK)]


async def list_commit_shas(
    session: AsyncSession, repository_id: str, where: CommitFilter | None = None
) -> list[str]:
    """Every commit sha under *where*; the commit table is bounded by ``commit_limit``."""
    rows = await session.execute(
        select(GitCommit.sha).where(
            GitCommit.repository_id == repository_id, *commit_filter_clauses(where)
        )
    )
    return list(rows.scalars())


async def get_commit_health_bulk(
    session: AsyncSession, repository_id: str, shas: Sequence[str]
) -> dict[str, GitCommitHealthDelta]:
    """``sha -> stored delta`` for the scanned commits among *shas*."""
    out: dict[str, GitCommitHealthDelta] = {}
    for chunk in _chunks(shas):
        rows = await session.execute(
            select(GitCommitHealthDelta).where(
                GitCommitHealthDelta.repository_id == repository_id,
                GitCommitHealthDelta.sha.in_(chunk),
            )
        )
        out.update({row.sha: row for row in rows.scalars()})
    return out


async def get_worse_paths_bulk(
    session: AsyncSession, repository_id: str, shas: Sequence[str]
) -> dict[str, set[str]]:
    """``sha -> paths its stored worse findings name`` (capped per commit at scan)."""
    out: dict[str, set[str]] = {}
    for chunk in _chunks(shas):
        rows = await session.execute(
            select(GitCommitHealthFinding.sha, GitCommitHealthFinding.file_path)
            .where(
                GitCommitHealthFinding.repository_id == repository_id,
                GitCommitHealthFinding.sha.in_(chunk),
            )
            .distinct()
        )
        for sha, path in rows.tuples():
            out.setdefault(sha, set()).add(path)
    return out


async def get_commit_findings_bulk(
    session: AsyncSession, repository_id: str, shas: Sequence[str], *, limit: int
) -> list[GitCommitHealthFinding]:
    """The stored worse findings of *shas*, worst-ranked first, at most *limit*.

    Each commit's ``position`` is its own worst-first rank, so ordering by it
    interleaves the commits' worst findings before anyone's minor ones.
    """
    found: list[GitCommitHealthFinding] = []
    for chunk in _chunks(shas):
        rows = await session.execute(
            select(GitCommitHealthFinding)
            .where(
                GitCommitHealthFinding.repository_id == repository_id,
                GitCommitHealthFinding.sha.in_(chunk),
            )
            .order_by(GitCommitHealthFinding.position, GitCommitHealthFinding.sha)
            .limit(limit)
        )
        found.extend(rows.scalars())
    found.sort(key=lambda f: (f.position, f.sha))
    return found[:limit]


async def count_hotspot_files_bulk(
    session: AsyncSession, repository_id: str, shas: Sequence[str]
) -> dict[str, int]:
    """``sha -> hotspot files it touched``; a sha that touched none is absent."""
    out: dict[str, int] = {}
    for chunk in _chunks(shas):
        rows = await session.execute(
            select(GitCommitFile.sha, func.count())
            .join(
                GitMetadata,
                and_(
                    GitMetadata.repository_id == GitCommitFile.repository_id,
                    GitMetadata.file_path == GitCommitFile.file_path,
                ),
            )
            .where(
                GitCommitFile.repository_id == repository_id,
                GitCommitFile.sha.in_(chunk),
                GitMetadata.is_hotspot.is_(True),
            )
            .group_by(GitCommitFile.sha)
        )
        out.update({sha: int(n) for sha, n in rows.tuples()})
    return out


async def get_hotspot_paths(session: AsyncSession, repository_id: str) -> set[str]:
    """Every path currently flagged a hotspot."""
    rows = await session.execute(
        select(GitMetadata.file_path).where(
            GitMetadata.repository_id == repository_id, GitMetadata.is_hotspot.is_(True)
        )
    )
    return set(rows.scalars())


async def get_resolving_commits(
    session: AsyncSession, repository_id: str, where: CommitFilter
) -> list[tuple[GitCommit, int]]:
    """``(commit, resolved_count)`` for commits under *where* that resolved a
    finding, most resolved first; current-stamp rows only (``where.stamp``)."""
    rows = await session.execute(
        select(GitCommit, GitCommitHealthDelta.resolved_count)
        .join(
            GitCommitHealthDelta,
            and_(
                GitCommitHealthDelta.repository_id == GitCommit.repository_id,
                GitCommitHealthDelta.sha == GitCommit.sha,
            ),
        )
        .where(
            GitCommit.repository_id == repository_id,
            GitCommitHealthDelta.resolved_count > 0,
            current_delta_clause(where.stamp),
            *commit_filter_clauses(where),
        )
        .order_by(GitCommitHealthDelta.resolved_count.desc(), GitCommit.committed_at.desc())
    )
    return [(commit, int(n)) for commit, n in rows.tuples()]


async def get_touched_paths(
    session: AsyncSession, repository_id: str, where: CommitFilter
) -> set[str]:
    """Every path a commit under *where* touched."""
    rows = await session.execute(
        select(GitCommitFile.file_path)
        .join(
            GitCommit,
            and_(
                GitCommit.repository_id == GitCommitFile.repository_id,
                GitCommit.sha == GitCommitFile.sha,
            ),
        )
        .where(GitCommitFile.repository_id == repository_id, *commit_filter_clauses(where))
        .distinct()
    )
    return set(rows.scalars())


async def get_snapshot_scores_at(
    session: AsyncSession, repository_id: str, at: datetime | None
) -> tuple[datetime, str] | None:
    """``(taken_at, per_file_scores_json)`` of the newest snapshot at or before
    *at* (the newest overall when *at* is ``None``), else the oldest kept one."""
    base = select(HealthSnapshot.taken_at, HealthSnapshot.per_file_scores_json).where(
        HealthSnapshot.repository_id == repository_id
    )
    newest_first = (HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
    if at is not None:
        row = (
            await session.execute(
                base.where(HealthSnapshot.taken_at <= at).order_by(*newest_first).limit(1)
            )
        ).first()
        if row is not None:
            return row[0], row[1]
        order: tuple[Any, ...] = (HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
    else:
        order = newest_first
    row = (await session.execute(base.order_by(*order).limit(1))).first()
    return (row[0], row[1]) if row is not None else None


async def get_applied_refactorings(
    session: AsyncSession,
    repository_id: str,
    *,
    since: datetime | None,
    until: datetime | None = None,
) -> list[tuple[RefactoringSuggestion, RefactoringPayoff]]:
    """Plans resolved as ``applied`` in the window, newest first."""
    # Deferred: the refactoring package imports crud.git, which imports this module.
    from ...analysis.health.refactoring.payoff import APPLIED

    stmt = (
        select(RefactoringSuggestion, RefactoringPayoff)
        .join(RefactoringPayoff, RefactoringPayoff.suggestion_id == RefactoringSuggestion.id)
        .where(
            RefactoringPayoff.repository_id == repository_id,
            RefactoringPayoff.outcome == APPLIED,
        )
        .order_by(RefactoringPayoff.resolved_at.desc(), RefactoringSuggestion.file_path)
    )
    if since is not None:
        stmt = stmt.where(RefactoringPayoff.resolved_at >= since)
    if until is not None:
        stmt = stmt.where(RefactoringPayoff.resolved_at < until)
    return list((await session.execute(stmt)).tuples())
