"""Co-change Scatter — files coupled to many others (shotgun surgery).

D'Ambros et al. found that a file co-changing with a *large number* of
distinct partners is a modest but real defect signal: every edit risks
rippling across the codebase. This is the breadth complement to
``hidden_coupling`` (which flags *specific* undeclared coupled pairs); here we
flag a file coupled to *many* others regardless of whether the links are
declared.

**Not the length of ``co_change_partners_json``.** That list is truncated to
the strongest ``MAX_PARTNERS_PER_FILE`` partners, so its length is a storage
cap: above it every file looks identical, and since the cap sat above the old
"high" cutoff, almost everything that fired fired high. A raw count also never
retires a partner the file stopped changing with, so it could only go up.

The gate is ``co_change_scatter_pct``, the repo-relative rank of
``co_change_mass`` — accumulated over every partner and decayed on a commit
clock. ``co_change_partner_count`` is the true total, for a human to read.

Fires when the file is actively changing and broadly coupled *for this repo*:

- ``co_change_scatter_pct`` ≥ 0.80 (top fifth), AND
- ``commit_count_90d`` ≥ 3.

Repo-relative, matching ``change_entropy`` and ``churn_risk``; the absolute
``scatter >= 8`` it replaces made this the one history biomarker whose reach
grew with the size and pace of the repo rather than staying a fixed share.

Test material and barrel files are exempt: both co-change with their subject by
design, which is why ``hidden_coupling`` exempts tests too.

Tier-aware: without the breadth columns the percentile is 0.0 and the detector
emits nothing — "no signal" rather than a guess off the truncated list.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ....co_change import parse_partners
from ....test_paths import is_test_related_path
from ...dead_code.file_reachability import BARREL_FILENAMES
from ..models import Severity
from ..semantics import format_top_percentile
from .base import BiomarkerResult, FileContext

_MIN_PERCENTILE = 0.80
_HIGH_PERCENTILE = 0.95
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


def _is_barrel(path: str) -> bool:
    """Whether *path* is a re-export barrel, by the repo's canonical name set.

    The filename alone, deliberately. Corroborating it structurally would be
    more precise but is unavailable to ``history_refresh``, which re-scores from
    git metadata without a parse, so the answer would depend on which pass ran.
    A barrel-named file that grew real logic loses this one signal and keeps
    every other biomarker.
    """
    return PurePosixPath(path).name in BARREL_FILENAMES


class CoChangeScatterDetector:
    name = "co_change_scatter"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta: dict[str, Any] = ctx.git_meta or {}

        if is_test_related_path(ctx.file_path, ctx.language) or _is_barrel(ctx.file_path):
            return []

        percentile = _as_float(meta.get("co_change_scatter_pct"))
        if percentile < _MIN_PERCENTILE:
            return []

        commits_90d = _as_int(meta.get("commit_count_90d"))
        if commits_90d < _MIN_COMMITS_90D:
            return []

        # Both numbers travel, so a reader can see when the stored list is a
        # truncation of the real breadth rather than all of it.
        partner_count = _as_int(meta.get("co_change_partner_count"))
        recorded = len(parse_partners(meta.get("co_change_partners_json")))
        scatter = partner_count or recorded

        severity = Severity.HIGH if percentile >= _HIGH_PERCENTILE else Severity.MEDIUM

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "scatter": scatter,
                    "recorded_partners": recorded,
                    "co_change_mass": round(_as_float(meta.get("co_change_mass")), 4),
                    # 0-100, as every other emitted percentile; the column is 0-1.
                    "co_change_scatter_pct": round(percentile * 100.0, 1),
                    "commit_count_90d": commits_90d,
                },
                reason=(
                    f"co-changes with {scatter} distinct files "
                    f"({format_top_percentile(percentile, 'files with any co-change history')}) "
                    "— editing this file tends to ripple across the codebase "
                    "(shotgun surgery)"
                ),
            )
        ]


BIOMARKER = CoChangeScatterDetector()
