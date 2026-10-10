"""The count vocabulary of each queue unit, read from the stored judgements.

Findings, performance causes and refactoring plans each carry their judgement
(``models.QueueVerdict``), so their counts are one grouped read. Fix first
items count from the stored queue. Every surface reads these, so a number
means the same thing in the CLI, MCP, REST and the web UI.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.queue.counts import NOT_JUDGED, QueueCounts, queue_counts

from ...models import HealthFinding, PerformanceOpportunity, RefactoringOpportunity
from .fix_first import load_fix_first, open_opportunities, queue_findings

Unit = Literal["findings", "causes", "plans", "items"]
#: One noun per unit, in the order surfaces list them.
UNITS: tuple[str, ...] = ("findings", "causes", "plans", "items")


def _population(unit: str, repo_id: str) -> tuple[Any, Any]:
    if unit == "findings":
        return HealthFinding, queue_findings(repo_id)
    if unit == "causes":
        p = PerformanceOpportunity
        # An older model's causes are resolved when the current one is written.
        return p, (p.repository_id == repo_id) & (p.status == "open")
    return RefactoringOpportunity, open_opportunities(repo_id)


async def unit_counts(
    session: AsyncSession,
    repo_id: str,
    unit: Unit,
    *,
    shown: int = 0,
    file_paths: Sequence[str] | None = None,
) -> QueueCounts:
    """``unit``'s counts over its open inventory, in ``file_paths`` when named.

    List filters (type, effort, context) never narrow these: the counts say
    where a list sits in the whole queue, and ``shown`` is what it carries.
    Fix first items are counted over the whole repository.
    """
    if unit == "items":
        # The whole queue: ``due`` reads every item's tier. Served from the
        # stored snapshot or the in-process cache, so no build.
        queue = await load_fix_first(session, repo_id, limit=None)
        return queue.counts(shown)
    model, population = _population(unit, repo_id)
    scoped = model.file_path.in_(list(file_paths)) if file_paths is not None else true()
    rows = await session.execute(
        select(model.queue_eligible, model.queue_reason, model.queue_tier, func.count())
        .where(population, scoped)
        .group_by(model.queue_eligible, model.queue_reason, model.queue_tier)
    )
    return queue_counts(
        (
            (NOT_JUDGED if eligible is None else None if eligible else reason, tier, n)
            for eligible, reason, tier, n in rows.all()
        ),
        shown,
    )


async def all_unit_counts(
    session: AsyncSession, repo_id: str, *, shown: dict[str, int] | None = None
) -> dict[str, dict[str, Any]]:
    """Every unit's counts, by noun, for a surface that reports them together."""
    out: dict[str, dict[str, Any]] = {}
    for unit in UNITS:
        counts = await unit_counts(session, repo_id, unit, shown=(shown or {}).get(unit, 0))  # type: ignore[arg-type]
        out[unit] = counts.as_dict()
    return out


__all__ = ["UNITS", "Unit", "all_unit_counts", "unit_counts"]
