"""What got better in a window, from stored data only. Positive by design.

Three wins are answerable without new history:

* findings resolved, from the stored per-commit deltas;
* files whose score rose, from the snapshot at the window's start and the
  newest one, counting only files a commit in the window touched;
* refactoring plans that landed, from the stored payoffs.

With an author every section narrows to that person's commits and the files
they touched; nothing is ranked against anyone else.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.history import parse_score_map, rising_files
from repowise.core.persistence import crud
from repowise.server.schemas.history import (
    WinsFileGain,
    WinsFilesImproved,
    WinsFindingsResolved,
    WinsRefactoring,
    WinsRefactorings,
    WinsResolvingCommit,
    WinsResponse,
)
from repowise.server.services.history.health import current_stamp

#: Wins the index keeps no history for, so it cannot say they happened.
UNAVAILABLE = (
    "dead_code_removed: dead-code findings are replaced on each index, not kept as history",
    "bus_factor_gains: ownership is stored as current state only",
    "resolved_finding_items: resolved findings are stored as per-commit counts, without the items",
)


async def _findings_resolved(
    session: AsyncSession, repo_id: str, where: crud.CommitFilter, top: int
) -> WinsFindingsResolved:
    rows = await crud.get_resolving_commits(session, repo_id, where)
    return WinsFindingsResolved(
        count=sum(n for _, n in rows),
        commits=len(rows),
        top_commits=[
            WinsResolvingCommit(
                sha=c.sha,
                subject=c.subject or "",
                author_name=c.author_name or "",
                committed_at=c.committed_at,
                resolved=n,
            )
            for c, n in rows[:top]
        ],
    )


async def _files_improved(
    session: AsyncSession,
    repo_id: str,
    since: datetime,
    until: datetime | None,
    touched: set[str],
    top: int,
) -> WinsFilesImproved:
    baseline = await crud.get_snapshot_scores_at(session, repo_id, since)
    current = await crud.get_snapshot_scores_at(session, repo_id, until)
    if baseline is None or current is None or baseline[0] == current[0]:
        return WinsFilesImproved(count=0)
    gains = rising_files(parse_score_map(baseline[1]), parse_score_map(current[1]), touched)
    return WinsFilesImproved(
        count=len(gains),
        baseline_at=baseline[0],
        current_at=current[0],
        top=[
            WinsFileGain(path=g.path, before=g.before, after=g.after, gain=g.gain)
            for g in gains[:top]
        ],
    )


async def _refactorings_landed(
    session: AsyncSession,
    repo_id: str,
    since: datetime,
    until: datetime | None,
    touched: set[str] | None,
    top: int,
) -> WinsRefactorings:
    landed = [
        (plan, payoff)
        for plan, payoff in await crud.get_applied_refactorings(
            session, repo_id, since=since, until=until
        )
        if touched is None or plan.file_path in touched
    ]
    return WinsRefactorings(
        count=len(landed),
        top=[
            WinsRefactoring(
                file_path=plan.file_path,
                target_symbol=plan.target_symbol,
                refactoring_type=plan.refactoring_type,
                resolved_at=payoff.resolved_at,
                before_ccn=payoff.before_ccn,
                after_ccn=payoff.after_ccn,
                before_nloc=payoff.before_nloc,
                after_nloc=payoff.after_nloc,
            )
            for plan, payoff in landed[:top]
        ],
    )


async def build_wins(
    session: AsyncSession,
    repo_id: str,
    *,
    since: datetime,
    until: datetime | None,
    author: str | None,
    top: int,
) -> WinsResponse:
    stamp = await current_stamp(session, repo_id)
    where = crud.CommitFilter(author=author, since=since, until=until, stamp=stamp)
    touched = await crud.get_touched_paths(session, repo_id, where)
    return WinsResponse(
        since=since,
        until=until,
        author=author,
        findings_resolved=await _findings_resolved(session, repo_id, where, top),
        files_improved=await _files_improved(session, repo_id, since, until, touched, top),
        # A plan's resolving run is whoever indexed next, not who did the work,
        # so an author is credited only with plans in files they touched.
        refactorings_landed=await _refactorings_landed(
            session, repo_id, since, until, touched if author else None, top
        ),
        unavailable=list(UNAVAILABLE),
    )
