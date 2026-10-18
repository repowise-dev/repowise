"""Health delta and hotspot touches for commit rows, and a commit period headline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.history import (
    DeltaStamp,
    HealthDeltaSummary,
    PeriodHealthSummary,
    summarize_period,
)
from repowise.core.persistence import crud

from .health import delta_response, stored_deltas


@dataclass(frozen=True, slots=True)
class CommitHistory:
    """What a page of commits did to health and how many hotspots each touched."""

    deltas: dict[str, HealthDeltaSummary]
    hotspots: dict[str, int]

    def fields(self, sha: str) -> dict:
        """The ``CommitResponse`` history fields for *sha*."""
        return {
            "health_delta": delta_response(self.deltas.get(sha)),
            "touches_hotspot": self.hotspots.get(sha, 0),
        }


async def commit_history(
    session: AsyncSession, repo_id: str, shas: Sequence[str], stamp: DeltaStamp
) -> CommitHistory:
    """Two bulk reads for the whole page, whatever its size. A row from an older
    analyzer shows, flagged stale."""
    return CommitHistory(
        deltas=await stored_deltas(session, repo_id, shas, stamp, current_only=False),
        hotspots=await crud.count_hotspot_files_bulk(session, repo_id, shas),
    )


async def commit_period_summary(
    session: AsyncSession, repo_id: str, where: crud.CommitFilter
) -> PeriodHealthSummary:
    """How many commits under *where* made health better, worse, both or
    neither; only current-analyzer rows count (``where.stamp``)."""
    if where.stamp is None:
        raise ValueError("a period summary needs the current analyzer stamp")
    shas = await crud.list_commit_shas(session, repo_id, where)
    deltas = await stored_deltas(session, repo_id, shas, where.stamp, current_only=True)
    return summarize_period(deltas.get(sha) for sha in shas)
