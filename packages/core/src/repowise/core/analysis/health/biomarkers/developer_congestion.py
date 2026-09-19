"""Developer Congestion — too many hands on a single file.

When a file has a large contributor count over a 90-day window and is
also a hotspot, coordination cost explodes. This pattern is strongly
correlated with regression rates in published industry studies.

Fires when ALL of:

- ``contributor_count`` ≥ 5
- ``churn_percentile`` ≥ 0.75 (the file is genuinely active *for this
  repository* — a fixed commit count is a different bar on a repository
  landing dozens a day than on one landing a handful a week)
- the primary owner's share is below 50% (no clear DRI)

Reads ``ctx.git_meta`` only; no AST work needed.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_CONTRIB_THRESHOLD = 5
_CHURN_PERCENTILE_FLOOR = 0.75
_OWNER_SHARE_THRESHOLD = 0.5


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


class DeveloperCongestionDetector:
    name = "developer_congestion"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta = ctx.git_meta or {}
        contributors = _as_int(meta.get("contributor_count"))
        commits_90d = _as_int(meta.get("commit_count_90d"))
        primary_pct = _as_float(meta.get("primary_owner_commit_pct"))

        if contributors < _CONTRIB_THRESHOLD:
            return []
        # "Actively changing" has to mean actively changing for this repository:
        # a fixed count of commits in 90 days is a different bar on a repository
        # landing a handful a week than on one landing dozens a day.
        if _as_float(meta.get("churn_percentile")) < _CHURN_PERCENTILE_FLOOR:
            return []
        # primary_owner_commit_pct may be stored as a 0-1 fraction or
        # 0-100 percentage depending on the source - normalize.
        share = primary_pct / 100.0 if primary_pct > 1.0 else primary_pct
        if share >= _OWNER_SHARE_THRESHOLD:
            return []

        if contributors >= 10 and commits_90d >= 20:
            severity = Severity.HIGH
        elif contributors >= 7 or commits_90d >= 12:
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
                    "contributor_count": contributors,
                    "commit_count_90d": commits_90d,
                    "primary_owner_share": round(share, 3),
                    "primary_owner": meta.get("primary_owner_name"),
                },
                reason=(
                    f"{contributors} contributors touched this file "
                    f"({commits_90d} commits in last 90 days, no clear primary owner)"
                ),
            )
        ]


BIOMARKER = DeveloperCongestionDetector()
