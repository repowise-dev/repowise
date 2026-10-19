"""Roll a change's commit deltas up, filter changes by them, and head a period.

A change keeps its commits as ``commit_shas_json`` and its files as
``files_json``; both are read with the change, and every commit delta, worse
finding path and hotspot flag the rollups need comes from one bulk read per
request.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.history import (
    DeltaStamp,
    HealthRollup,
    PeriodHealthSummary,
    matches_delta,
    rollup_deltas,
    summarize_period,
)
from repowise.core.persistence import crud
from repowise.server.schemas.changes import ChangeHealthFindingResponse, ChangeHealthResponse

from .health import finding_fields, rollup_fields, stored_deltas

#: Worse findings a change detail carries for a fix prompt; the counts are whole.
DETAIL_FINDINGS = 50


@dataclass(frozen=True, slots=True)
class ChangeInputs:
    """What a change's rollup reads: its commits and its stored file paths."""

    change_id: str
    shas: tuple[str, ...]
    paths: tuple[str, ...]

    @classmethod
    def parse(cls, change_id: str, commit_shas_json: str, files_json: str) -> ChangeInputs:
        files = _loads_list(files_json)
        return cls(
            change_id,
            tuple(str(s) for s in _loads_list(commit_shas_json)),
            tuple(f["path"] for f in files if isinstance(f, dict) and f.get("path")),
        )


def _loads_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


@dataclass(frozen=True, slots=True)
class ChangeHealth:
    rollup: HealthRollup | None
    touches_hotspot: int
    # The commits the rollup counted, so detail findings come from the same rows.
    counted_shas: tuple[str, ...] = ()


async def change_health(
    session: AsyncSession, repo_id: str, changes: Sequence[ChangeInputs], stamp: DeltaStamp
) -> dict[str, ChangeHealth]:
    """``change_id -> rollup and hotspot touches`` for *changes*, in three reads.

    A rollup is an aggregate: rows under the current *stamp* only, the rest unscanned.
    """
    shas = [sha for c in changes for sha in c.shas]
    deltas = await stored_deltas(session, repo_id, shas, stamp, current_only=True)
    worse_paths = await crud.get_worse_paths_bulk(session, repo_id, list(deltas))
    hotspots = await crud.get_hotspot_paths(session, repo_id)
    out: dict[str, ChangeHealth] = {}
    for c in changes:
        rollup = rollup_deltas(
            [deltas.get(sha) for sha in c.shas],
            (path for sha in c.shas for path in worse_paths.get(sha, ())),
        )
        out[c.change_id] = ChangeHealth(
            rollup,
            sum(1 for p in set(c.paths) if p in hotspots),
            tuple(sha for sha in c.shas if sha in deltas),
        )
    return out


async def _matching_inputs(
    session: AsyncSession, repo_id: str, where: crud.ChangeFilter
) -> list[ChangeInputs]:
    rows = await crud.list_change_health_inputs(session, repo_id, where)
    return [ChangeInputs.parse(*row) for row in rows]


async def narrow_by_health(
    session: AsyncSession,
    repo_id: str,
    where: crud.ChangeFilter,
    stamp: DeltaStamp,
    *,
    delta: str | None,
    touches_hotspot: bool,
) -> crud.ChangeFilter:
    """*where* narrowed to the changes whose rollup passes *delta* and *touches_hotspot*.

    Both are judged on the rollup, so the matching ids are fixed here and the
    caller's own listing (plain or needs-attention) still orders and pages.
    A change no commit of which was scanned passes no delta filter.

    Reads every matching change's commits and files, then binds the kept ids
    as one ``IN`` list. Bounded by the change window (``commit_limit``); the
    ceiling is the driver's bind limit (32k on SQLite, 65k on PostgreSQL),
    past which this would move into SQL over a stored per-change rollup.
    """
    if not delta and not touches_hotspot:
        return where
    inputs = await _matching_inputs(session, repo_id, where)
    health = await change_health(session, repo_id, inputs, stamp)
    keep = [
        c.change_id
        for c in inputs
        if _passes(health[c.change_id], delta=delta, touches_hotspot=touches_hotspot)
    ]
    return replace(where, change_ids=keep)


def _passes(health: ChangeHealth, *, delta: str | None, touches_hotspot: bool) -> bool:
    if touches_hotspot and not health.touches_hotspot:
        return False
    if not delta:
        return True
    return health.rollup is not None and matches_delta(health.rollup.kind, delta)


async def change_period_summary(
    session: AsyncSession,
    repo_id: str,
    where: crud.ChangeFilter,
    stamp: DeltaStamp,
    *,
    delta: str | None = None,
    touches_hotspot: bool = False,
) -> PeriodHealthSummary:
    """How many changes under the filters made health better, worse, both or neither."""
    inputs = await _matching_inputs(session, repo_id, where)
    health = await change_health(session, repo_id, inputs, stamp)
    return summarize_period(
        health[c.change_id].rollup
        for c in inputs
        if _passes(health[c.change_id], delta=delta, touches_hotspot=touches_hotspot)
    )


async def change_detail_health(
    session: AsyncSession, repo_id: str, change: ChangeInputs, stamp: DeltaStamp
) -> tuple[ChangeHealthResponse | None, int]:
    """A change detail's health block, with its worse findings, and hotspot touches."""
    got = (await change_health(session, repo_id, [change], stamp))[change.change_id]
    if got.rollup is None:
        return None, got.touches_hotspot
    rows = await crud.get_commit_findings_bulk(
        session, repo_id, got.counted_shas, limit=DETAIL_FINDINGS
    )
    findings = [ChangeHealthFindingResponse(sha=f.sha, **finding_fields(f)) for f in rows]
    return ChangeHealthResponse(**rollup_fields(got.rollup), findings=findings), got.touches_hotspot
