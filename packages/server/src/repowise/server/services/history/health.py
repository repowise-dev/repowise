"""Stored commit deltas read in bulk, and their wire shapes.

Shared by the commit and change surfaces so both read a delta the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.history import (
    HealthDeltaSummary,
    HealthRollup,
    PeriodHealthSummary,
    summarize_delta,
)
from repowise.core.persistence import crud
from repowise.core.persistence.sql import is_missing_table
from repowise.server.schemas.history import (
    FileScoreShiftResponse,
    HealthDeltaSummaryResponse,
    HealthRollupResponse,
    PeriodHealthSummaryResponse,
)


async def stored_deltas(
    session: AsyncSession, repo_id: str, shas: Sequence[str]
) -> dict[str, HealthDeltaSummary]:
    """``sha -> delta`` for the scanned commits among *shas*; empty on an index
    older than the delta table."""
    try:
        rows = await crud.get_commit_health_bulk(session, repo_id, shas)
    except (OperationalError, ProgrammingError) as exc:
        if not is_missing_table(exc):
            raise
        return {}
    # Seam: per-file score shifts and the improved count join here once the
    # stored delta rows carry the analyzer's health_delta.files[] and improved
    # bucket; summarize_delta is the one reader to extend.
    return {sha: summarize_delta(row) for sha, row in rows.items()}


def delta_response(delta: HealthDeltaSummary | None) -> HealthDeltaSummaryResponse | None:
    if delta is None:
        return None
    return HealthDeltaSummaryResponse(
        status=delta.status,
        kind=delta.kind,
        introduced=delta.introduced,
        worsened=delta.worsened,
        resolved=delta.resolved,
        improved=delta.improved,
        files=[
            FileScoreShiftResponse(path=f.path, before=f.before, after=f.after, change=f.change)
            for f in delta.files
        ],
    )


def finding_fields(row: Any) -> dict:
    """``CommitHealthFindingResponse`` fields of a stored ``GitCommitHealthFinding``."""
    return {
        "change_kind": row.change_kind,
        "dimension": row.dimension,
        "biomarker_type": row.biomarker_type,
        "severity": row.severity,
        "severity_before": row.severity_before,
        "path": row.file_path,
        "symbol": row.symbol,
        "line_start": row.line_start,
        "line_end": row.line_end,
        "attribution_basis": row.attribution_basis,
        "reason": row.reason,
    }


def rollup_fields(rollup: HealthRollup) -> dict:
    """The :class:`HealthRollupResponse` fields of *rollup*."""
    return {**asdict(rollup), "kind": rollup.kind}


def rollup_response(rollup: HealthRollup | None) -> HealthRollupResponse | None:
    return None if rollup is None else HealthRollupResponse(**rollup_fields(rollup))


def period_response(
    summary: PeriodHealthSummary, since: datetime | None, until: datetime | None
) -> PeriodHealthSummaryResponse:
    return PeriodHealthSummaryResponse(since=since, until=until, **asdict(summary))
