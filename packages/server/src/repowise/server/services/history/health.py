"""Stored commit deltas read in bulk, and their wire shapes.

Shared by the commit and change surfaces so both read a delta the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.history import (
    DeltaStamp,
    HealthDeltaSummary,
    HealthRollup,
    PeriodHealthSummary,
    delta_currency,
    summarize_delta,
)
from repowise.core.persistence import crud
from repowise.core.persistence.sql import is_missing_table
from repowise.server.schemas.git import MovedFromResponse
from repowise.server.schemas.history import (
    FileScoreShiftResponse,
    HealthDeltaSummaryResponse,
    HealthRollupResponse,
    PeriodHealthSummaryResponse,
)


async def current_stamp(session: AsyncSession, repo_id: str) -> DeltaStamp:
    """The analyzer stamp a delta computed now would carry (a config hash, ~5 ms)."""
    from repowise.core.analysis.change_health.commit_scan import current_fingerprint

    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return DeltaStamp(**current_fingerprint(repo.local_path))


async def stored_deltas(
    session: AsyncSession,
    repo_id: str,
    shas: Sequence[str],
    stamp: DeltaStamp,
    *,
    current_only: bool,
) -> dict[str, HealthDeltaSummary]:
    """``sha -> delta`` for the scanned commits among *shas*; empty on an index
    older than the delta table.

    *current_only* (every aggregate) keeps rows with the current *stamp* and
    treats the rest as never scanned. Otherwise (one row on display) an older
    readable row comes back flagged ``stale``; one below the shown floor is absent.
    """
    try:
        rows = await crud.get_commit_health_bulk(session, repo_id, shas)
    except (OperationalError, ProgrammingError) as exc:
        if not is_missing_table(exc):
            raise
        return {}
    out: dict[str, HealthDeltaSummary] = {}
    for sha, row in rows.items():
        currency = delta_currency(row, stamp)
        if currency == "current" or (currency == "stale" and not current_only):
            out[sha] = summarize_delta(row, stale=currency == "stale")
    return out


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
        moved=delta.moved,
        stale=delta.stale,
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
        "moved_from": (
            MovedFromResponse(path=row.moved_from_path, symbol=row.moved_from_symbol)
            if row.moved_from_path
            else None
        ),
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
