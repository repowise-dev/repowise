"""Change Entropy — files whose changes are scattered across noisy commits.

Hassan ("Predicting Faults Using the Complexity of Code Changes", ICSE 2009)
showed that the *entropy* of a file's change history — how spread-out and
chaotic its modifications are over time — predicts faults (r≈0.55-0.78),
independently of raw churn. A file repeatedly caught up in wide, scattered
commits accumulates a high History Complexity Metric; one changed in focused,
single-purpose commits stays low even if it changes often.

The git indexer computes the decay-weighted per-file HCM during the co-change
walk (``git_meta["change_entropy"]``) and its repo-wide percentile
(``change_entropy_pct``). This biomarker is the consumer.

Fires when the file is both actively changing and in the top entropy band:

- ``change_entropy_pct`` ≥ 0.80, AND
- ``commit_count_90d`` ≥ 3 (entropy only matters for files in motion).

Tier-aware: on the ESSENTIAL git tier (and for files that never co-changed)
``change_entropy`` is 0 and its percentile 0.0, so the detector stays silent.
"""

from __future__ import annotations

from typing import Any

from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_PERCENTILE = 0.80
_HIGH_PERCENTILE = 0.90
_CRITICAL_PERCENTILE = 0.95
_MIN_COMMITS_90D = 3


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class ChangeEntropyDetector:
    name = "change_entropy"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta: dict[str, Any] = ctx.git_meta or {}

        entropy = _as_float(meta.get("change_entropy"))
        # No entropy signal → ESSENTIAL tier or a file that only ever changed
        # alone. Treat as no signal rather than guessing.
        if entropy <= 0.0:
            return []

        percentile = _as_float(meta.get("change_entropy_pct"))
        commits_90d = _as_int(meta.get("commit_count_90d"))
        if percentile < _MIN_PERCENTILE or commits_90d < _MIN_COMMITS_90D:
            return []

        is_hotspot = bool(meta.get("is_hotspot"))
        if percentile >= _CRITICAL_PERCENTILE and is_hotspot:
            severity = Severity.CRITICAL
        elif percentile >= _HIGH_PERCENTILE:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "change_entropy": round(entropy, 4),
                    "change_entropy_pct": round(percentile, 3),
                    "commit_count_90d": commits_90d,
                },
                reason=(
                    f"changes are scattered across noisy commits "
                    f"(top {(1 - percentile) * 100:.0f}% change entropy); "
                    "a strong history-based fault predictor"
                ),
            )
        ]


BIOMARKER = ChangeEntropyDetector()
