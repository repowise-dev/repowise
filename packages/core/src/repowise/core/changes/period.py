"""Every change merged in a period with its checklist, in one pass.

:func:`period_evidence` is the one reader ``repowise changes export`` and
``repowise report changes`` share. The period's changes are fixed up front
(ids only), so changes indexed while it runs neither shift nor repeat a page.
A kept checklist is read back as
:func:`~repowise.core.changes.collect.change_evidence` reads it (its outcomes
follow HEAD); a missing one is built and kept. What every build would repeat
is done once for the period: the HEAD and window outcomes are read at, and the
deep fix-history walk the risk item stands on (one ``git log`` primes every
change's walk, see
:func:`~repowise.core.analysis.change_risk.fix_history.prime_fix_pressure`).
Builds in a page run side by side, each on its own read session, and each is
kept the moment it finishes, so an interrupt or a failed build loses only the
builds still running. A checklist another writer kept meanwhile (the server,
an outcomes merge) is never overwritten: it is read back instead.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import structlog

from repowise.core.changes import collect
from repowise.core.changes.evidence import ChangeEvidence

logger = structlog.get_logger(__name__)

#: Changes read per page; at most the fix-pressure memo's size.
PAGE = 16
#: Checklists built at once. Most of a build is git and parsing in worker
#: threads, so more than a few contend for the interpreter rather than help.
DEFAULT_CONCURRENCY = 4
#: Wall seconds per checklist built at :data:`DEFAULT_CONCURRENCY`, measured on
#: a mid-size repository (rw-demo, 32 changes); used only for an estimate.
SECONDS_PER_BUILD = 3.0
#: Pages in a row the fix-history walk may answer nothing before it is dropped.
_PRIME_MISSES = 3

#: ``(position in the period, changes in the period, change id)`` per build.
Progress = Callable[[int, int, str], None]
#: ``(checklists to build, changes in the period)``, told once before any build.
Plan = Callable[[int, int], None]


class _Prime:
    """One fix-history walk for the period, primed page by page."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.history: Any = None
        self.useful = True
        self.misses = 0

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
        parents = [p[0] for r in rows if (p := self.history.parents.get(r.head_sha))]
        if not parents or prime_fix_pressure(self.root, self.history, parents):
            self.misses = 0
            return
        self.misses += 1
        # Older changes reach fewer commits, so a miss can be followed by hits;
        # but once a page's oldest change lies outside the walk, so do all older.
        if self.misses >= _PRIME_MISSES or not self.history.answers(rows[-1].head_sha):
            self.useful = False


async def _keep(session: Any, repo_id: str, row: Any, evidence: ChangeEvidence) -> ChangeEvidence:
    """Keep *evidence* unless another writer kept a checklist since *row* was
    read; then that one is what the change has, and is returned."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import set_committed_value

    from repowise.core.persistence.crud import keep_evidence_if_unchanged
    from repowise.core.persistence.crud.changes import dump_evidence
    from repowise.core.persistence.models import Change

    raw = dump_evidence(evidence.to_dict())
    if not await keep_evidence_if_unchanged(
        session, repo_id, row.change_id, raw, expected=row.evidence_json
    ):
        raw = (
            await session.execute(
                select(Change.evidence_json).where(
                    Change.repository_id == repo_id, Change.change_id == row.change_id
                )
            )
        ).scalar_one_or_none()
        evidence = collect.cached_evidence(SimpleNamespace(evidence_json=raw)) or evidence
    await session.commit()
    set_committed_value(row, "evidence_json", raw)
    return evidence


async def _build_page(
    session: Any,
    repo_id: str,
    root: Path,
    rows: Sequence[Any],
    *,
    concurrency: int,
    progress: Progress | None,
    position: Callable[[Any], tuple[int, int]],
) -> dict[str, ChangeEvidence]:
    """Build *rows* side by side, keeping each as it finishes. A failed build
    is raised once the others in flight have finished and been kept."""
    from sqlalchemy.ext.asyncio import AsyncSession

    gate = asyncio.Semaphore(max(1, concurrency))
    write = asyncio.Lock()  # the caller's session takes one writer at a time

    async def one(row: Any) -> tuple[str, ChangeEvidence]:
        async with gate, AsyncSession(session.bind, expire_on_commit=False) as reader:
            evidence = await collect.build_stored_evidence(reader, repo_id, root, row)
        async with write:
            evidence = await _keep(session, repo_id, row, evidence)
        if progress is not None:
            progress(*position(row), row.change_id)
        return row.change_id, evidence

    done = await asyncio.gather(*(one(r) for r in rows), return_exceptions=True)
    failed = [d for d in done if isinstance(d, BaseException)]
    if failed:
        raise failed[0]
    return dict(done)  # type: ignore[arg-type]


async def period_evidence(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    *,
    since: datetime | None,
    until: datetime | None,
    refresh: bool = False,
    build: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress: Progress | None = None,
    plan: Plan | None = None,
) -> AsyncIterator[tuple[Any, ChangeEvidence | None]]:
    """``(row, checklist)`` for each change merged in ``[since, until)``, newest first.

    *refresh* rebuilds every checklist. Without *build* nothing is built, and
    a change with no kept checklist comes with ``None``. *plan* is told how
    many checklists will be built before the first starts; *progress* is told
    about each build.
    """
    from repowise.core.persistence.crud import ChangeFilter, list_change_keys, list_changes

    root = Path(repo_root)
    keys = await list_change_keys(session, repo_id, ChangeFilter(since=since, until=until))
    total = len(keys)
    if plan is not None:
        plan(sum(refresh or not kept for _cid, kept in keys) if build else 0, total)
    key = await collect.outcomes_key(root)
    prime = _Prime(str(root))
    for start in range(0, total, PAGE):
        ids = [cid for cid, _kept in keys[start : start + PAGE]]
        found, _ = await list_changes(session, repo_id, ChangeFilter(change_ids=ids), limit=None)
        by_id = {r.change_id: r for r in found}
        rows = [by_id[cid] for cid in ids if cid in by_id]  # one deleted meanwhile drops out
        missing = [r for r in rows if refresh or collect.cached_evidence(r) is None]
        built: dict[str, ChangeEvidence] = {}
        if missing and build:
            await asyncio.to_thread(prime, missing)
            at = {cid: (start + i + 1, total) for i, cid in enumerate(ids)}
            built = await _build_page(
                session,
                repo_id,
                root,
                missing,
                concurrency=concurrency,
                progress=progress,
                position=lambda r, at=at: at[r.change_id],
            )
        page: list[tuple[Any, ChangeEvidence | None]] = []
        for row in rows:
            if row.change_id in built:
                page.append((row, built[row.change_id]))
            elif collect.cached_evidence(row) is None:
                page.append((row, None))
            else:
                evidence = await collect.change_evidence(session, repo_id, root, row, key=key)
                page.append((row, evidence))
        await session.commit()
        for pair in page:
            yield pair
        session.expunge_all()


__all__ = [
    "DEFAULT_CONCURRENCY",
    "PAGE",
    "SECONDS_PER_BUILD",
    "Plan",
    "Progress",
    "period_evidence",
]
