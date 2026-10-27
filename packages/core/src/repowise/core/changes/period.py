"""Every change merged in a period with its checklist, in one pass.

:func:`period_evidence` is the one reader ``repowise changes export`` and
``repowise report changes`` share. The period's changes are fixed up front
(ids only), so changes indexed while it runs neither shift nor repeat a page.
A kept checklist is read back as
:func:`~repowise.core.changes.collect.change_evidence` reads it (its outcomes
follow HEAD, its components the catalog); a missing one is built and kept. What every build would repeat
is done once for the period: the HEAD and window outcomes are read at, and the
deep fix-history walk the risk item stands on (one ``git log`` primes every
change's walk, see
:func:`~repowise.core.analysis.change_risk.fix_history.prime_fix_pressure`).
With no start date a period covers the :data:`DEFAULT_WINDOW_DAYS` before the
newest indexed commit (see :meth:`PeriodRequest.resolved`), so a first run on a
long history does not build a checklist for every change it ever merged.
Builds in a page run side by side, each on its own read session, and each is
kept the moment it finishes, so an interrupt or a failed build loses only the
builds still running. A checklist another writer kept meanwhile (the server,
an outcomes merge) is never overwritten: it is read back instead.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import structlog

from repowise.core.changes import collect
from repowise.core.changes.evidence import ChangeEvidence
from repowise.core.events.snapshot import RECENT_DAYS

logger = structlog.get_logger(__name__)

#: Changes read per page; at most the fix-pressure memo's size.
PAGE = 16
#: Checklists built at once. Most of a build is git and parsing in worker
#: threads, so more than a few contend for the interpreter rather than help.
DEFAULT_CONCURRENCY = 4
#: Wall seconds per checklist built at :data:`DEFAULT_CONCURRENCY`, measured on
#: a mid-size repository (rw-demo, 32 changes); used only for an estimate.
SECONDS_PER_BUILD = 3.0
#: Days a period with no start date covers, back from the newest indexed commit
#: (the recent-activity window the rest of the index measures).
DEFAULT_WINDOW_DAYS = RECENT_DAYS
#: Pages in a row the fix-history walk may answer nothing before it is dropped.
_PRIME_MISSES = 3

#: ``(position in the period, changes in the period, change id)`` per build.
Progress = Callable[[int, int, str], None]
#: ``(checklists to build, changes in the period)``, told once before any build.
Plan = Callable[[int, int], None]


@dataclass(frozen=True)
class PeriodRequest:
    """Which changes a period pass reads, and how it builds missing checklists.

    *since* is inclusive and *until* exclusive, on ``merged_at``; ``None``
    leaves that side open, except that a request with no *since* and a
    *window_days* starts that many days before the newest indexed commit (or
    *until*, when earlier); ``window_days=None`` covers every indexed change.
    *refresh* rebuilds every checklist; without *build*
    nothing is built, and a change with no kept checklist comes with ``None``.
    *plan* is told how many checklists will be built before the first starts;
    *progress* is told about each build.
    """

    since: datetime | None = None
    until: datetime | None = None
    window_days: int | None = DEFAULT_WINDOW_DAYS
    refresh: bool = False
    build: bool = True
    concurrency: int = DEFAULT_CONCURRENCY
    progress: Progress | None = None
    plan: Plan | None = None

    async def resolved(self, session: Any, repo_id: str) -> PeriodRequest:
        """This request with its start date fixed. *window_days* is kept only
        when the start came from it, so a caller can say which period it read."""
        from sqlalchemy import func, select

        from repowise.core.persistence.models import GitCommit

        if self.since is not None or not self.window_days:
            return replace(self, window_days=None)
        newest = await session.scalar(
            select(func.max(GitCommit.committed_at)).where(GitCommit.repository_id == repo_id)
        )
        if newest is None:  # nothing indexed to measure back from
            return replace(self, window_days=None)
        anchor = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
        if self.until is not None and self.until < anchor:
            anchor = self.until
        return replace(self, since=anchor - timedelta(days=self.window_days))


@dataclass(frozen=True)
class _Pass:
    """One period pass over one repository's open index."""

    session: Any
    repo_id: str
    root: Path
    request: PeriodRequest
    total: int


class _Prime:
    """One fix-history walk for the period, primed page by page."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.history: Any = None
        self.useful = True
        self.misses = 0

    def _walk(self) -> Any:
        """The period's walk, read on first use; ``None`` (given up) once git fails."""
        from repowise.core.analysis.change_risk.fix_history import FixHistory

        if self.history is None and self.useful:
            try:
                self.history = FixHistory(self.root)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("period_fix_history_failed", error=str(exc))
                self.useful = False
        return self.history if self.useful else None

    def __call__(self, rows: Sequence[Any]) -> None:
        from repowise.core.analysis.change_risk.fix_history import prime_fix_pressure

        history = self._walk()
        if history is None:
            return
        parents = [p[0] for r in rows if (p := history.parents.get(r.head_sha))]
        if not parents or prime_fix_pressure(self.root, history, parents):
            self.misses = 0
            return
        self.misses += 1
        # Older changes reach fewer commits, so a miss can be followed by hits;
        # but once a page's oldest change lies outside the walk, so do all older.
        self.useful = self.misses < _PRIME_MISSES and history.answers(rows[-1].head_sha)


async def _keep(run: _Pass, row: Any, evidence: ChangeEvidence) -> ChangeEvidence:
    """Keep *evidence* unless another writer kept a checklist since *row* was
    read; then that one is what the change has, and is returned."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import set_committed_value

    from repowise.core.persistence.crud import keep_evidence_if_unchanged
    from repowise.core.persistence.crud.changes import dump_evidence
    from repowise.core.persistence.models import Change

    raw = dump_evidence(evidence.to_dict())
    if not await keep_evidence_if_unchanged(
        run.session, run.repo_id, row.change_id, raw, expected=row.evidence_json
    ):
        raw = (
            await run.session.execute(
                select(Change.evidence_json).where(
                    Change.repository_id == run.repo_id, Change.change_id == row.change_id
                )
            )
        ).scalar_one_or_none()
        evidence = collect.cached_evidence(SimpleNamespace(evidence_json=raw)) or evidence
    await run.session.commit()
    set_committed_value(row, "evidence_json", raw)
    return evidence


async def _build_page(
    run: _Pass, rows: Sequence[Any], position: Callable[[Any], int]
) -> dict[str, ChangeEvidence]:
    """Build *rows* side by side, keeping each as it finishes. A failed build
    is raised once the others in flight have finished and been kept."""
    from sqlalchemy.ext.asyncio import AsyncSession

    gate = asyncio.Semaphore(max(1, run.request.concurrency))
    write = asyncio.Lock()  # the caller's session takes one writer at a time
    progress = run.request.progress

    async def one(row: Any) -> tuple[str, ChangeEvidence]:
        async with gate, AsyncSession(run.session.bind, expire_on_commit=False) as reader:
            evidence = await collect.build_stored_evidence(reader, run.repo_id, run.root, row)
        async with write:
            evidence = await _keep(run, row, evidence)
        if progress is not None:
            progress(position(row), run.total, row.change_id)
        return row.change_id, evidence

    done = await asyncio.gather(*(one(r) for r in rows), return_exceptions=True)
    failed = [d for d in done if isinstance(d, BaseException)]
    if failed:
        raise failed[0]
    return dict(done)  # type: ignore[arg-type]


async def _page_rows(run: _Pass, ids: list[str]) -> list[Any]:
    """The rows of *ids*, in that order, read in one query; one deleted meanwhile drops out."""
    from repowise.core.persistence.crud import ChangeFilter, list_changes

    found, _ = await list_changes(
        run.session, run.repo_id, ChangeFilter(change_ids=ids), limit=None
    )
    by_id = {r.change_id: r for r in found}
    return [by_id[cid] for cid in ids if cid in by_id]


async def _read_kept(
    run: _Pass, rows: Sequence[Any], key: Any, catalog: str
) -> dict[str, ChangeEvidence | None]:
    """Each row's kept checklist (``None`` when none is kept), from the page
    already read. Only a checklist whose outcomes were read at another HEAD,
    or whose components were resolved under another catalog, is read again."""
    kept = {r.change_id: collect.cached_evidence(r) for r in rows}
    stale = [
        r
        for r in rows
        if (got := kept[r.change_id]) is not None
        and (
            await collect.outcomes_stale(run.root, got, key)
            or await collect.components_stale(run.root, got, catalog)
        )
    ]
    for row in stale:
        kept[row.change_id] = await collect.change_evidence(
            run.session, run.repo_id, run.root, row, key=key, catalog=catalog
        )
    return kept


async def period_evidence(
    session: Any, repo_id: str, repo_root: Path | str, request: PeriodRequest
) -> AsyncIterator[tuple[Any, ChangeEvidence | None]]:
    """``(row, checklist)`` for each change *request* names, newest first."""
    from repowise.core.persistence.crud import ChangeFilter, list_change_keys

    request = await request.resolved(session, repo_id)
    keys = await list_change_keys(
        session, repo_id, ChangeFilter(since=request.since, until=request.until)
    )
    run = _Pass(session, repo_id, Path(repo_root), request, len(keys))
    if request.plan is not None:
        to_build = sum(request.refresh or not kept for _cid, kept in keys)
        request.plan(to_build if request.build else 0, run.total)
    key = await collect.outcomes_key(run.root)
    catalog = await asyncio.to_thread(collect.catalog_digest, run.root)
    prime = _Prime(str(run.root))
    for start in range(0, run.total, PAGE):
        ids = [cid for cid, _kept in keys[start : start + PAGE]]
        rows = await _page_rows(run, ids)
        missing = [r for r in rows if request.refresh or collect.cached_evidence(r) is None]
        built: dict[str, ChangeEvidence] = {}
        if missing and request.build:
            await asyncio.to_thread(prime, missing)
            built = await _build_page(
                run, missing, lambda r, s=start, ids=ids: s + ids.index(r.change_id) + 1
            )
        kept = await _read_kept(
            run, [r for r in rows if r.change_id not in built], key, catalog
        )
        await session.commit()
        for row in rows:
            yield row, built.get(row.change_id) or kept.get(row.change_id)
        session.expunge_all()


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_WINDOW_DAYS",
    "PAGE",
    "SECONDS_PER_BUILD",
    "PeriodRequest",
    "Plan",
    "Progress",
    "period_evidence",
]
