"""Every change merged in a period with its checklist, in one pass.

:func:`period_evidence` is the one reader ``repowise changes export`` and
``repowise report changes`` share. A kept checklist is read back as
:func:`~repowise.core.changes.collect.change_evidence` reads it (its
outcomes follow HEAD); a missing one is built and kept, a page at a time. What
every build would repeat is done once for the period: the HEAD and window
outcomes are read at, and the deep fix-history walk the risk item stands on
(one ``git log`` primes every change's walk, see
:func:`~repowise.core.analysis.change_risk.fix_history.prime_fix_pressure`).
Builds in a page run side by side, each on its own read session; the page is
kept in one commit, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from repowise.core.changes import collect
from repowise.core.changes.evidence import ChangeEvidence

logger = structlog.get_logger(__name__)

#: Changes read and kept per page; at most the fix-pressure memo's size.
PAGE = 16
#: Checklists built at once. Most of a build is git and parsing in worker
#: threads, so more than a few contend for the interpreter rather than help.
DEFAULT_CONCURRENCY = 4

#: ``(position in the period, changes in the period, change id)`` per build.
Progress = Callable[[int, int, str], None]


class _Prime:
    """One fix-history walk for the period, primed page by page."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.history: Any = None
        self.useful = True

    def __call__(self, rows: Sequence[Any]) -> None:
        from repowise.core.analysis.change_risk.fix_history import (
            FixHistory,
            prime_fix_pressure,
        )

        if not self.useful:
            return
        try:
            if self.history is None:
                self.history = FixHistory(self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("period_fix_history_failed", error=str(exc))
            self.useful = False
            return
        parents = [
            p[0] for r in rows if (p := self.history.parents.get(r.head_sha))
        ]
        # A history deeper than the walk limit answers nothing; stop trying.
        if parents and not prime_fix_pressure(self.root, self.history, parents):
            self.useful = False


async def _build_page(
    session: Any,
    repo_id: str,
    root: Path,
    rows: Sequence[Any],
    concurrency: int,
    progress: Progress | None,
    position: Callable[[Any], tuple[int, int]],
) -> dict[str, ChangeEvidence]:
    from sqlalchemy.ext.asyncio import AsyncSession

    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(row: Any) -> tuple[str, ChangeEvidence]:
        async with gate:
            # Reads only; the caller keeps every result in its own session.
            async with AsyncSession(session.bind, expire_on_commit=False) as reader:
                evidence = await collect.build_stored_evidence(reader, repo_id, root, row)
            if progress is not None:
                progress(*position(row), row.change_id)
            return row.change_id, evidence

    return dict(await asyncio.gather(*(one(r) for r in rows)))


async def period_evidence(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    *,
    since: datetime | None,
    until: datetime | None,
    refresh: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress: Progress | None = None,
) -> AsyncIterator[tuple[Any, ChangeEvidence]]:
    """``(row, checklist)`` for each change merged in ``[since, until)``, newest first.

    *refresh* rebuilds every checklist. *progress* is told about each build.
    """
    from repowise.core.persistence.crud import ChangeFilter, list_changes
    from repowise.core.persistence.crud.changes import dump_evidence

    root = Path(repo_root)
    key = await collect.outcomes_key(root)
    prime = _Prime(str(root))
    where = ChangeFilter(since=since, until=until)
    done = 0
    while True:
        rows, total = await list_changes(session, repo_id, where, limit=PAGE, offset=done)
        missing = [r for r in rows if refresh or collect.cached_evidence(r) is None]
        built: dict[str, ChangeEvidence] = {}
        if missing:
            await asyncio.to_thread(prime, missing)
            at = {r.change_id: (done + i + 1, total) for i, r in enumerate(rows)}
            built = await _build_page(
                session, repo_id, root, missing, concurrency, progress, lambda r, at=at: at[r.change_id]
            )
        page: list[tuple[Any, ChangeEvidence]] = []
        for row in rows:
            if row.change_id in built:
                evidence = built[row.change_id]
                row.evidence_json = dump_evidence(evidence.to_dict())
            else:
                evidence = await collect.change_evidence(session, repo_id, root, row, key=key)
            page.append((row, evidence))
        # Kept before it is handed on, so an interrupted run resumes here.
        await session.commit()
        for pair in page:
            yield pair
        done += len(rows)
        session.expunge_all()
        if len(rows) < PAGE:
            return


__all__ = ["DEFAULT_CONCURRENCY", "PAGE", "Progress", "period_evidence"]
