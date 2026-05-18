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
or a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

DECLINE_THRESHOLD: float = 0.5
DECLINE_LOOKBACK: int = 5  # compare current vs snapshot N positions back
PREDICTED_DECLINE_CONSECUTIVE: int = 3


class SnapshotLike(Protocol):
    """Minimal shape the trend logic needs from a snapshot row."""

    taken_at: datetime
    hotspot_health: float
    average_health: float
    worst_performer_path: str | None
    worst_performer_score: float | None


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
