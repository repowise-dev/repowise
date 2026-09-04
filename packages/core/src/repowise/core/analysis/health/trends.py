"""Snapshot diffing + trend alerts.

Consumed by:
  * the snapshot writer (``persistence/crud.py.save_health_snapshot``) which
    feeds in the rolling window
  * the ``repowise health --trend`` CLI flag (prints the 10 most recent
    snapshots' KPIs side-by-side)
  * the MCP ``get_health(include=["trend"])`` response

Two alert kinds are emitted, matching plan §4 Phase 4 P4.1:

  * ``declining`` — current ``hotspot_health`` is ≥ ``DECLINE_THRESHOLD``
    points (default 0.5) below the snapshot N-5 entries ago. This catches
    sustained drops, not single-snapshot noise.
  * ``predicted_decline`` — the three most recent snapshots are each
    strictly below the one before them. Magnitude is not required —
    direction is the signal.

The module is intentionally state-free. Callers pass in the snapshot
history (oldest → newest) and receive a list of alerts back. No DB
access lives here so trend logic stays unit-testable without an engine
or a session. The one exception is ``_parsed_map`` below, a bounded
content-keyed cache over ``json.loads`` of a snapshot's per-file map — a pure
cache with no observable effect on any return value.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, TypeAlias

from .scoring import SCORE_FLOOR, SCORE_MAX

DECLINE_THRESHOLD: float = 0.5
DECLINE_LOOKBACK: int = 5  # compare current vs snapshot N positions back
PREDICTED_DECLINE_CONSECUTIVE: int = 3


@dataclass
class TrendAlert:
    """A single trend signal worth surfacing on the dashboard / CLI."""

    kind: str  # "declining" | "predicted_decline"
    metric: str  # "hotspot_health" | "average_health"
    current: float
    baseline: float | None
    delta: float
    message: str


@dataclass
class TrendSummary:
    """Lightweight diff between the newest snapshot and the prior window."""

    current_hotspot_health: float
    current_average_health: float
    previous_hotspot_health: float | None
    previous_average_health: float | None
    hotspot_delta: float | None
    average_delta: float | None
    alerts: list[TrendAlert] = field(default_factory=list)


def _delta(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    return round(current - previous, 3)


def diff_snapshots(history: list[Any]) -> TrendSummary:
    """Compare the newest snapshot against the window behind it.

    *history* is expected oldest-first (the natural insertion order in
    ``HealthSnapshot``). Empty history yields a summary with neutral
    fields and no alerts.
    """
    if not history:
        return TrendSummary(
            current_hotspot_health=10.0,
            current_average_health=10.0,
            previous_hotspot_health=None,
            previous_average_health=None,
            hotspot_delta=None,
            average_delta=None,
        )

    current = history[-1]
    prior = history[-2] if len(history) >= 2 else None
    summary = TrendSummary(
        current_hotspot_health=float(current.hotspot_health),
        current_average_health=float(current.average_health),
        previous_hotspot_health=float(prior.hotspot_health) if prior else None,
        previous_average_health=float(prior.average_health) if prior else None,
        hotspot_delta=_delta(
            float(current.hotspot_health),
            float(prior.hotspot_health) if prior else None,
        ),
        average_delta=_delta(
            float(current.average_health),
            float(prior.average_health) if prior else None,
        ),
    )

    summary.alerts.extend(_declining_alerts(history))
    summary.alerts.extend(_predicted_decline_alerts(history))
    return summary


def _declining_alerts(history: list[Any]) -> list[TrendAlert]:
    """``Declining Health`` — current is ≥ threshold below snapshot N-5."""
    if len(history) <= DECLINE_LOOKBACK:
        return []
    current = history[-1]
    baseline = history[-1 - DECLINE_LOOKBACK]
    out: list[TrendAlert] = []
    for metric in ("hotspot_health", "average_health"):
        cur_val = float(getattr(current, metric))
        base_val = float(getattr(baseline, metric))
        delta = round(cur_val - base_val, 3)
        if delta <= -DECLINE_THRESHOLD:
            out.append(
                TrendAlert(
                    kind="declining",
                    metric=metric,
                    current=round(cur_val, 2),
                    baseline=round(base_val, 2),
                    delta=delta,
                    message=(
                        f"{metric.replace('_', ' ').title()} dropped "
                        f"{abs(delta):.2f} points vs. snapshot "
                        f"{DECLINE_LOOKBACK} ago "
                        f"({base_val:.2f} → {cur_val:.2f})."
                    ),
                )
            )
    return out


def _predicted_decline_alerts(history: list[Any]) -> list[TrendAlert]:
    """``Predicted Decline`` — N consecutive strict drops, any magnitude."""
    needed = PREDICTED_DECLINE_CONSECUTIVE + 1
    if len(history) < needed:
        return []
    tail = history[-needed:]
    out: list[TrendAlert] = []
    for metric in ("hotspot_health", "average_health"):
        vals = [float(getattr(s, metric)) for s in tail]
        if all(vals[i + 1] < vals[i] for i in range(len(vals) - 1)):
            delta = round(vals[-1] - vals[0], 3)
            out.append(
                TrendAlert(
                    kind="predicted_decline",
                    metric=metric,
                    current=round(vals[-1], 2),
                    baseline=round(vals[0], 2),
                    delta=delta,
                    message=(
                        f"{metric.replace('_', ' ').title()} declined for "
                        f"{PREDICTED_DECLINE_CONSECUTIVE} consecutive snapshots "
                        f"({vals[0]:.2f} → {vals[-1]:.2f})."
                    ),
                )
            )
    return out


def recent_kpis(history: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    """Serialize the most-recent *limit* snapshots for CLI / API consumers.

    Newest first (so the CLI table reads top-down chronologically when
    flipped, which matches user expectation for "recent runs"). Each row
    is a plain dict — no ORM leakage.
    """
    if not history:
        return []
    tail = history[-limit:]
    rows: list[dict[str, Any]] = []
    for snap in reversed(tail):
        rows.append(
            {
                "taken_at": snap.taken_at.isoformat() if snap.taken_at else None,
                "hotspot_health": round(float(snap.hotspot_health), 2),
                "average_health": round(float(snap.average_health), 2),
                "worst_performer_path": snap.worst_performer_path,
                "worst_performer_score": (
                    round(float(snap.worst_performer_score), 2)
                    if snap.worst_performer_score is not None
                    else None
                ),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Per-file trajectory
#
# The snapshot writer stores two compact maps per snapshot: ``{path: score}``
# (``HealthSnapshot.per_file_scores_json``) for every scored file, and
# ``{path: total_deduction}`` (``per_file_deductions_json``) for the files whose
# score is held at ``SCORE_FLOOR``. These helpers turn that rolling window into
# a single file's trajectory. Like the repo-level helpers above they are
# intentionally state-free: callers pass the snapshot history (oldest → newest)
# and get plain data back, so the logic stays unit-testable without a DB and is
# reused verbatim by the PR bot's in-comment sparkline.
# --------------------------------------------------------------------------- #


def snapshot_file_maps(
    metrics: list[Any],
    findings: list[Any],
) -> tuple[dict[str, float], dict[str, float]]:
    """Build both of a snapshot's per-file maps from one health report.

    Returns ``(per_file_scores, per_file_deductions)``, ready to hand to
    ``save_health_snapshot``.

    One function because there are three writers — ``repowise health``,
    ``repowise upgrade`` and the full-index pipeline — and a repo's history
    would otherwise carry different data depending on which of them wrote the
    row last. That failure has happened here before, with a different argument
    and four call sites.

    ``per_file_deductions`` holds only the files whose score sits at
    ``SCORE_FLOOR``. For every other file the deduction is exactly
    ``SCORE_MAX - score``, so recording it would be storing a value the reader
    can already compute — on the repowise index, 2,083 B of floored files
    against 142,812 B if every file with findings were written, beside a
    187,593 B score map. The deduction is summed from ``health_impact``, which
    is the per-finding contribution the scorer used to produce ``score``, so
    the pair is consistent by construction rather than by reconciliation.

    Note the recorded depth is the **category-capped** total, so it is itself
    bounded by ``sum(CATEGORY_CAPS)`` and the unclamped score cannot go below
    ``SCORE_MAX - sum(CATEGORY_CAPS)``. A file with every category at its cap
    is flat again, one level down. Nothing on the repowise index is close
    (deepest is 12.91 of a possible 13.5), and recording the pre-cap sum
    instead would be recording a number the score was never computed from.
    """
    per_file_scores = {m.file_path: round(float(m.score), 2) for m in metrics}

    totals: dict[str, float] = {}
    for f in findings:
        path = getattr(f, "file_path", None)
        if not path:
            continue
        totals[path] = totals.get(path, 0.0) + float(getattr(f, "health_impact", 0.0) or 0.0)

    per_file_deductions = {
        m.file_path: round(totals[m.file_path], 2)
        for m in metrics
        if float(m.score) <= SCORE_FLOOR and m.file_path in totals
    }
    return per_file_scores, per_file_deductions


#: One normalized snapshot reading for a single file: when it was taken, the
#: clamped score, and the recorded pre-clamp deduction where the reading
#: captured one. The storage-neutral input to :func:`build_file_points`.
FileScoreReading: TypeAlias = tuple[datetime | None, float, float | None]

#: Readings needed before a per-file series is worth drawing.
MIN_TREND_POINTS: int = 2


@dataclass
class FileTrendPoint:
    """One file's score at one snapshot.

    ``score`` is the clamped, surfaced number. ``unclamped_score`` is
    ``SCORE_MAX - total_deduction`` where the snapshot recorded a deduction,
    and ``score`` otherwise — so it is the series that keeps moving after the
    floor is hit, and is identical to ``score`` for every file that has not hit
    it and for every row written before deductions were captured.

    Required rather than defaulted: a point that silently defaults to its own
    score is exactly the flat line this field exists to stop, and it would be
    indistinguishable from a real one at the read site.
    """

    taken_at: datetime | None
    score: float
    unclamped_score: float


@dataclass
class FileTrend:
    """A file's score trajectory + the deltas worth surfacing.

    ``points`` is oldest-first and **empty when fewer than two snapshots
    carry the file** — a per-file trend is silent on thin history rather than
    drawing a misleading single dot (plan §2: "silent when < 2 real
    snapshots"). ``current`` / ``previous`` / ``delta`` and ``declining``
    are all ``None`` / ``False`` in that case.

    ``current`` / ``previous`` / ``delta`` stay on the clamped score — that is
    the number printed everywhere else in the product and changing it would
    make the trend disagree with the file's own header. ``unclamped_delta`` is
    the movement the floor hides, and equals ``delta`` whenever the floor is
    not involved, so a consumer can read it unconditionally.
    """

    file_path: str
    points: list[FileTrendPoint]
    current: float | None
    previous: float | None
    delta: float | None
    declining: bool
    snapshot_count: int
    unclamped_delta: float | None = None


# A snapshot's ``{path: value}`` map is a per-*repo* blob — one JSON object
# holding every file's score — and this module reads it one file at a time. So a
# caller asking about N files paid ``N x len(history)`` parses of the same few
# blobs: the cross-function N+1 the health pillar's own ``json_parse_in_loop``
# biomarker exists to flag, in the health pillar. Measured on the repowise index
# (20 snapshots averaging 186 KB each), ``get_health`` on a ``module:`` target
# expanding to 822 files spent **13.0s** here; memoized, 0.4s.
#
# Keyed on the raw JSON **text**, not on the snapshot object. Content-keying
# makes staleness impossible by construction (different bytes are a different
# key), needs no hashable/weak-referenceable snapshot — a plain dataclass in a
# test is neither — and lets two callers holding different rows for the same
# stored snapshot share one parse. The window a caller reads is 20 snapshots x 2
# maps, so ``maxsize`` covers a full window with headroom; the ceiling is that a
# long-lived server holds up to 64 parsed maps, which is what bounds it.
@lru_cache(maxsize=64)
def _parsed_map(raw: str) -> Any:
    """``json.loads(raw)``, memoized. ``None`` when the text will not parse."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _value_in_snapshot(snap: Any, column: str, file_path: str) -> float | None:
    """Read one float out of a snapshot's compact ``{path: value}`` JSON map.

    Returns ``None`` when the file is absent from that map (it may have been
    added later, renamed, filtered out of that run, or — for the deductions map
    — simply not be at the floor) or when the map can't be parsed.
    """
    raw = getattr(snap, column, None)
    if not raw:
        return None
    parsed = _parsed_map(raw)
    val = parsed.get(file_path) if isinstance(parsed, dict) else None
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _score_in_snapshot(snap: Any, file_path: str) -> float | None:
    """``file_path``'s clamped score in *snap*, or ``None`` if not recorded."""
    return _value_in_snapshot(snap, "per_file_scores_json", file_path)


def _unclamped_score(score: float, deduction: float | None) -> float | None:
    """One reading's score with the floor undone, as far as it is known.

    Three cases, and the third is the one that matters:

    * A recorded deduction means the clamp bit and the reading kept the depth.
      The honest value is ``SCORE_MAX - deduction``, below the floor and
      possibly negative.
    * No deduction and a score above the floor: the clamp did not bite, so the
      score already *is* the unclamped value.
    * No deduction and a score **at** the floor: the depth is unknown. The row
      predates deduction capture, or the file had no findings to sum. Returns
      ``None`` — not ``score``, which would assert a depth of exactly 9.0 that
      was never measured.
    """
    if deduction is not None:
        return round(SCORE_MAX - deduction, 2)
    return score if score > SCORE_FLOOR else None


def build_file_points(
    readings: Sequence[FileScoreReading],
    *,
    min_points: int = MIN_TREND_POINTS,
) -> list[FileTrendPoint]:
    """Turn normalized readings into a file's oldest-first trend series.

    The correctness half of a per-file trend, over storage-neutral readings, so
    that where the numbers came from — a snapshot row here, something else
    elsewhere — stays entirely inside the adapter that produced them.

    Returns ``[]`` below *min_points*, so a consumer renders a "no history yet"
    state instead of a single misleading dot. The threshold is a parameter
    rather than a constant because it is a presentation floor, not a
    correctness one: two is right for a chart, and a consumer that can surface a
    lone reading honestly may ask for one.

    ``unclamped_score`` diverges from ``score`` only when **every** reading has
    a known depth. A series that mixes measured depth with readings that never
    recorded it is the dangerous case, not the harmless one: the unmeasured
    points read as ``1.0`` and the measured ones as their real depth, so the
    first index after depth capture is switched on draws a cliff on a file that
    did not change. Measured against the live index — fifteen snapshots without
    capture plus one with, and nothing altered — that flipped **21 of 32**
    floored files to ``declining`` with drops up to 3.9 points. Waiting for the
    window to fill costs a slow start; not waiting reports a collapse that
    never happened.
    """
    if len(readings) < min_points:
        return []
    depths = [_unclamped_score(score, deduction) for _, score, deduction in readings]
    depth_known = all(depth is not None for depth in depths)
    return [
        FileTrendPoint(
            taken_at=taken_at,
            score=score,
            unclamped_score=depth if depth_known and depth is not None else score,
        )
        for (taken_at, score, _), depth in zip(readings, depths, strict=True)
    ]


def _snapshot_readings(history: list[Any], file_path: str) -> list[FileScoreReading]:
    """Normalize one file out of the snapshot window. The storage adapter."""
    readings: list[FileScoreReading] = []
    for snap in history:
        score = _score_in_snapshot(snap, file_path)
        if score is None:
            continue
        readings.append(
            (
                getattr(snap, "taken_at", None),
                score,
                _value_in_snapshot(snap, "per_file_deductions_json", file_path),
            )
        )
    return readings


def file_score_series(history: list[Any], file_path: str) -> list[FileTrendPoint]:
    """A file's oldest-first score series across the snapshot window.

    Snapshots missing the file are skipped (gaps don't break the line).
    *history* is expected oldest-first (the natural ``list_health_snapshots``
    order). This is the exact function the PR bot reuses for its in-comment
    sparkline, so it stays free of any persistence or presentation concern.
    """
    return build_file_points(_snapshot_readings(history, file_path))


def _file_declining(points: list[FileTrendPoint]) -> bool:
    """A sustained-decline heuristic for a single file's series.

    Mirrors the repo-level signals: fire when either the latest score is
    ``DECLINE_THRESHOLD`` below the point ``DECLINE_LOOKBACK`` back, or the
    tail is ``PREDICTED_DECLINE_CONSECUTIVE`` strict drops in a row. Single
    snapshot-to-snapshot noise is deliberately not enough.

    Computed on ``unclamped_score``, which is identical to ``score`` for every
    file that has not hit the floor — so nothing changes for the 99% — and is
    the only series that can move for one that has. There is deliberately **no**
    third ``at_floor`` state: the original complaint was that ``declining:
    false`` sat beside a visibly collapsed series, and that was a disagreement
    between the flag and the *clamped* line, not a missing state. Against the
    unclamped line a flat flag now describes a flat series, and a file that
    keeps getting worse below the floor trips the flag for the first time —
    which is the behaviour the flag always claimed to have.
    """
    if len(points) <= 1:
        return False
    scores = [p.unclamped_score for p in points]

    if (
        len(scores) > DECLINE_LOOKBACK
        and scores[-1] <= scores[-1 - DECLINE_LOOKBACK] - DECLINE_THRESHOLD
    ):
        return True

    needed = PREDICTED_DECLINE_CONSECUTIVE + 1
    if len(scores) >= needed:
        tail = scores[-needed:]
        if all(tail[i + 1] < tail[i] for i in range(len(tail) - 1)):
            return True
    return False


def file_trend_from_points(
    file_path: str,
    readings: Sequence[FileScoreReading],
    *,
    snapshot_count: int,
    min_points: int = MIN_TREND_POINTS,
) -> FileTrend:
    """Assemble a :class:`FileTrend` from normalized readings.

    The whole per-file trend contract with no storage in it. *snapshot_count*
    is the size of the caller's whole window rather than of *readings*, so a
    consumer can tell "young repo" from "file absent from older snapshots".

    ``previous``, ``delta`` and ``unclamped_delta`` need two points and stay
    ``None`` on a series shorter than that — which only a caller passing
    ``min_points=1`` can see.
    """
    points = build_file_points(readings, min_points=min_points)
    if not points:
        return FileTrend(
            file_path=file_path,
            points=[],
            current=None,
            previous=None,
            delta=None,
            declining=False,
            snapshot_count=snapshot_count,
        )
    current = round(points[-1].score, 2)
    if len(points) < 2:
        return FileTrend(
            file_path=file_path,
            points=points,
            current=current,
            previous=None,
            delta=None,
            declining=False,
            snapshot_count=snapshot_count,
        )
    previous = round(points[-2].score, 2)
    return FileTrend(
        file_path=file_path,
        points=points,
        current=current,
        previous=previous,
        delta=round(current - previous, 2),
        unclamped_delta=round(points[-1].unclamped_score - points[-2].unclamped_score, 2),
        declining=_file_declining(points),
        snapshot_count=snapshot_count,
    )


def file_trend(history: list[Any], file_path: str) -> FileTrend:
    """A file's :class:`FileTrend` over the snapshot window.

    The snapshot adapter over :func:`file_trend_from_points`.
    """
    return file_trend_from_points(
        file_path,
        _snapshot_readings(history, file_path),
        snapshot_count=len(history),
    )
