"""Churn Risk — a file being rewritten faster than its size.

Nagappan & Ball (ICSE 2005) found that *relative* churn — code churn
normalized by file size — predicts defect density ~89% accurately,
while raw churn is a weak predictor. A file whose 90-day window rewrote
more lines than the file contains is structurally unstable regardless
of how large it is.

The trigger is a *ratio* to NLOC, so this does not simply re-flag big
files — that is the point. It is the size-normalized counterpart to the
raw-activity signals, designed to survive the partial-Spearman / NLOC
control.

Fires when the file is actively and disproportionately churning
relative to its size:

- ``commit_count_90d`` ≥ 5, AND
- ``churn_percentile`` ≥ 0.75 (top quartile of repo-relative churn), AND
- ``relative_churn`` ≥ 1.0 (the 90-day window rewrote ≥100% of lines).

Reads ``git_meta`` only. When git indexing was skipped the fields are
absent/zero and the detector emits nothing.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_COMMITS_90D = 5
_CHURN_PCT_FLOOR = 0.75
_RELATIVE_CHURN_FLOOR = 1.0


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class ChurnRiskDetector:
    name = "churn_risk"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        commits_90d = _as_int(meta.get("commit_count_90d"))
        if commits_90d < _MIN_COMMITS_90D:
            return []

        churn_pct = _as_float(meta.get("churn_percentile"))
        if churn_pct < _CHURN_PCT_FLOOR:
            return []

        added = _as_int(meta.get("lines_added_90d"))
        deleted = _as_int(meta.get("lines_deleted_90d"))
        nloc = max(ctx.nloc, 1)
        relative_churn = (added + deleted) / nloc
        if relative_churn < _RELATIVE_CHURN_FLOOR:
            return []

        # relative_churn is always >= _RELATIVE_CHURN_FLOOR (1.0) here — below it
        # the detector already returned. Above 100% the churn/NLOC ratio reads as
        # nonsense as a percentage (e.g. "15475%"), so render it as a multiplier
        # of the file's size instead.
        churn_text = f"{relative_churn:.1f}x the file's size"

        is_hotspot = bool(meta.get("is_hotspot"))
        if relative_churn >= 4 and is_hotspot:
            severity = Severity.CRITICAL
        elif relative_churn >= 2.5:
            severity = Severity.HIGH
        elif relative_churn >= 1.5:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "relative_churn": round(relative_churn, 2),
                    "churn_percentile": round(churn_pct, 3),
                    "lines_added_90d": added,
                    "lines_deleted_90d": deleted,
                    "commit_count_90d": commits_90d,
                    "nloc": ctx.nloc,
                },
                reason=(
                    f"90-day churn rewrote {churn_text} "
                    f"({added + deleted} lines over {ctx.nloc} NLOC, "
                    f"top {(1 - churn_pct):.0%} of repo churn)"
                ),
            )
        ]


BIOMARKER = ChurnRiskDetector()
