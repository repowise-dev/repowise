"""Health snapshots: the rolling per-repo history and its narrow reads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ....models import DocDriftFinding, HealthSnapshot, _new_uuid, _now_utc

# Rolling history kept per repo. Older snapshots are deleted on insert.
# 50 entries gives Phase 4's `--trend` flag (last 10) plus the 5-back
# Declining-Health baseline plenty of headroom.
HEALTH_SNAPSHOT_RETENTION: int = 50

#: How many snapshots a *single file's* trend line reads. Every snapshot row
#: carries two whole-repo ``{path: float}`` maps, so a route that wants one
#: path's score series pays the entire retention window in TEXT to extract a
#: handful of floats. Twenty points is more than the decline heuristics need
#: (``DECLINE_LOOKBACK`` is 5) and more than the sparkline can usefully draw.
#:
#: One knock-on: ``FileTrend.snapshot_count`` is documented as the size of the
#: whole repo window, and on the two routes that pass this it becomes the size
#: of *this* window instead. Nothing reads that field off a per-file trend
#: today — the dashboard and the trend view read it off the repo-level routes,
#: which are unlimited and unchanged. Projecting one path's series in SQL would
#: keep the count true and drop the bytes further, but no query in this module
#: extracts from a JSON column and doing that portably across SQLite and
#: PostgreSQL is its own change.
FILE_TREND_SNAPSHOT_WINDOW: int = 20


async def _doc_drift_count(session: AsyncSession, repository_id: str) -> int | None:
    """Stored drift findings; ``None`` when the drift pass has never run here."""
    from ..doc_drift import doc_drift_pass_ran

    count = await session.scalar(
        select(func.count(DocDriftFinding.id)).where(
            DocDriftFinding.repository_id == repository_id
        )
    )
    if not count and not await doc_drift_pass_ran(session, repository_id):
        return None
    return int(count or 0)


async def save_health_snapshot(
    session: AsyncSession,
    repository_id: str,
    *,
    hotspot_health: float | None,
    average_health: float,
    worst_performer_path: str | None,
    worst_performer_score: float | None,
    per_file_scores: dict[str, float] | None = None,
    per_file_deductions: dict[str, float] | None = None,
    structure_average: float | None = None,
    history_average: float | None = None,
    production_average: float | None = None,
    maintainability_average: float | None = None,
    taken_at: datetime | None = None,
) -> HealthSnapshot:
    """Append a snapshot; prune oldest rows past ``HEALTH_SNAPSHOT_RETENTION``.

    Returns the inserted row. Per-file scores are stored compactly as
    ``{path: score}`` JSON (no per-finding detail — that lives in
    ``HealthFinding`` rows; snapshots are a thin history layer).

    ``per_file_deductions`` is the same shape for the files whose score is held
    at the floor, where the stored score has stopped carrying information.
    Build both with ``trends.snapshot_file_maps`` rather than by hand: a repo
    whose writers disagree gets a history that changes depth depending on which
    command last wrote it.

    ``structure_average`` / ``history_average`` are ``average_health``'s two
    halves in deduction points, so a later trend can name which one moved.

    The stored doc drift count and the analyzer version are read here rather
    than passed in, so every writer records them without a new argument; write
    drift before calling this.
    """
    from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION

    snap = HealthSnapshot(
        id=_new_uuid(),
        repository_id=repository_id,
        taken_at=taken_at or _now_utc(),
        hotspot_health=float(hotspot_health) if hotspot_health is not None else None,
        average_health=float(average_health),
        worst_performer_path=worst_performer_path,
        worst_performer_score=(
            float(worst_performer_score) if worst_performer_score is not None else None
        ),
        per_file_scores_json=json.dumps(per_file_scores or {}, separators=(",", ":")),
        per_file_deductions_json=json.dumps(per_file_deductions or {}, separators=(",", ":")),
        structure_average=structure_average,
        history_average=history_average,
        production_average=production_average,
        maintainability_average=maintainability_average,
        doc_drift_count=await _doc_drift_count(session, repository_id),
        analyzer_version=HEALTH_ANALYZER_VERSION,
    )
    session.add(snap)
    await session.flush()

    # Prune older-than-retention rows. We keep the *N* newest by
    # ``taken_at``; ties are broken by id (UUIDs are random but stable).
    rows = await session.execute(
        select(HealthSnapshot)
        .where(HealthSnapshot.repository_id == repository_id)
        .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
    )
    history = list(rows.scalars().all())
    if len(history) > HEALTH_SNAPSHOT_RETENTION:
        for row in history[HEALTH_SNAPSHOT_RETENTION:]:
            await session.delete(row)
        await session.flush()
    return snap


async def list_health_snapshots(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int | None = None,
    file_maps: bool = True,
) -> list[HealthSnapshot]:
    """Return snapshots **oldest-first** (the shape ``trends.diff_snapshots``
    expects). Pass ``limit`` to cap the most recent N (still returned
    oldest-first for stable iteration).

    ``file_maps=False`` defers both per-file maps, for a caller that reads the
    scalars of every row and the maps of a few: it loads those with
    ``session.refresh(row, ["per_file_scores_json"])``.

    ``limit`` is pushed into SQL rather than applied to the fetched list. Every
    row carries ``per_file_scores_json`` and ``per_file_deductions_json``, both
    whole-repo ``{path: float}`` maps, and the table keeps
    ``HEALTH_SNAPSHOT_RETENTION`` (50) of them — so trimming in Python read the
    entire history off disk to throw most of it away. Taking the newest N
    descending and reversing gives the same list for a fraction of the bytes.
    """
    base = select(HealthSnapshot).where(HealthSnapshot.repository_id == repository_id)
    if not file_maps:
        base = base.options(
            defer(HealthSnapshot.per_file_scores_json),
            defer(HealthSnapshot.per_file_deductions_json),
        )
    if limit is not None:
        newest_first = await session.execute(
            base.order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc()).limit(limit)
        )
        return list(reversed(newest_first.scalars().all()))
    q = base.order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
    result = await session.execute(q)
    return list(result.scalars().all())


class HealthSnapshotScalars(NamedTuple):
    """One snapshot's numeric columns, with no per-file map behind them."""

    taken_at: datetime | None
    average_health: float
    hotspot_health: float | None


class HealthSnapshotHeadline(NamedTuple):
    """The snapshot figures a dashboard header actually reads.

    ``recent`` is empty unless the caller asked for it, and is ordered
    **oldest-first** to match ``list_health_snapshots`` — a trend line reads
    left to right, and the newest row is ``recent[-1]``.
    """

    hotspot_health: float | None
    taken_at: datetime | None
    snapshot_count: int
    recent: tuple[HealthSnapshotScalars, ...] = ()


async def get_health_snapshot_headline(
    session: AsyncSession,
    repository_id: str,
    *,
    recent: int = 0,
) -> HealthSnapshotHeadline:
    """Latest snapshot's ``hotspot_health`` / ``taken_at``, plus how many exist.

    ``list_health_snapshots`` hydrates whole entities, and every row carries a
    ``per_file_scores_json`` blob sized by the repo's file count — on this
    codebase that is ~186 KB each, 2.8 MB across the retained history and ~9 MB
    at ``HEALTH_SNAPSHOT_RETENTION``. A caller that only needs scalars was
    reading all of it to use a handful of floats.

    Pass ``recent=N`` for the newest N rows' scalars as well — a sparkline and a
    delta-vs-previous both want a bounded window of numbers, not the history.
    ``snapshot_count`` is always the **true** total, independent of ``recent``,
    so a capped window can never be mistaken for the whole history.

    No JSON column is touched. Callers that genuinely need the per-file maps
    (the trend routes, the file drawer's sparkline) should keep using
    ``list_health_snapshots``; a caller that needs only their *sizes* wants
    ``get_health_snapshot_file_counts``.
    """
    rows = (
        await session.execute(
            select(
                HealthSnapshot.hotspot_health,
                HealthSnapshot.taken_at,
                HealthSnapshot.average_health,
            )
            .where(HealthSnapshot.repository_id == repository_id)
            .order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
        )
    ).all()
    if not rows:
        return HealthSnapshotHeadline(None, None, 0)
    hotspot_health, taken_at, _ = rows[-1]
    window = rows[-recent:] if recent > 0 else []
    return HealthSnapshotHeadline(
        hotspot_health,
        taken_at,
        len(rows),
        tuple(
            HealthSnapshotScalars(r_taken_at, float(r_avg), r_hotspot)
            for r_hotspot, r_taken_at, r_avg in window
        ),
    )


async def get_latest_snapshot_analyzer_version(
    session: AsyncSession, repository_id: str
) -> int | None:
    """The analyzer version of the newest snapshot; ``None`` when unrecorded."""
    return await session.scalar(
        select(HealthSnapshot.analyzer_version)
        .where(HealthSnapshot.repository_id == repository_id)
        .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
        .limit(1)
    )


async def get_health_snapshot_file_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int = 2,
) -> list[int]:
    """How many files the newest *limit* snapshots scored, oldest-first.

    The only thing the overview's ``file_count`` delta wants out of
    ``per_file_scores_json`` is its key count, and it compares two rows. Loading
    the whole retained history to measure two of its maps read 2.8 MB to use
    375 KB of it.

    Ordering matches ``list_health_snapshots`` exactly, so "the newest two"
    cannot mean different rows here than there.

    Anything that is not a ``{path: score}`` object counts 0 — unparseable, but
    also a bare JSON array or string, which ``len()`` would happily measure into
    a file count meaning nothing. The caller reads 0 as "unknown" and omits the
    delta rather than publishing a number it cannot justify. This is deliberately
    stricter than the ``len(json.loads(...))`` it replaced;
    ``save_health_snapshot`` only ever writes an object, so it can only bite on
    rows that did not come from it.
    """
    if limit <= 0:
        return []
    rows = (
        await session.execute(
            select(HealthSnapshot.per_file_scores_json)
            .where(HealthSnapshot.repository_id == repository_id)
            .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
            .limit(limit)
        )
    ).all()
    counts: list[int] = []
    for (blob,) in reversed(rows):
        try:
            parsed = json.loads(blob or "{}")
        except (TypeError, ValueError):
            counts.append(0)
            continue
        counts.append(len(parsed) if isinstance(parsed, dict) else 0)
    return counts
