"""Co-change Scatter — files coupled to many others (shotgun surgery).

D'Ambros et al. found that a file co-changing with a *large number* of
distinct partners is a modest but real defect signal: every edit risks
rippling across the codebase. This is the breadth complement to
``hidden_coupling`` (which flags *specific* undeclared coupled pairs); here we
flag a file coupled to *many* others regardless of whether the links are
declared.

Reads ``git_meta["co_change_partners_json"]`` (the partner list the git indexer
already stores). **scatter** = the number of distinct partners recorded for the
file, which the indexer has already filtered to pairs sharing real commits.

Fires when the file is actively changing and broadly coupled:

- ``scatter`` ≥ 8 (shotgun-surgery territory), AND
- ``commit_count_90d`` ≥ 3.

Tier-aware: when ``co_change_partners_json`` is empty (ESSENTIAL git tier) the
detector emits nothing.
"""

from __future__ import annotations

from typing import Any

from ....co_change import parse_partners
from ..models import Severity
from .base import BiomarkerResult, FileContext

_SCATTER_THRESHOLD = 8
_HIGH_SCATTER = 15
_MIN_COMMITS_90D = 3


def _count_scatter(meta: dict[str, Any]) -> int:
    # No second cutoff here: the indexer already dropped pairs that share too
    # few commits, so every recorded partner counts.
    return len(parse_partners(meta.get("co_change_partners_json")))


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class CoChangeScatterDetector:
    name = "co_change_scatter"
    category = "organizational"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        meta: dict[str, Any] = ctx.git_meta or {}

        scatter = _count_scatter(meta)
        if scatter < _SCATTER_THRESHOLD:
            return []

        commits_90d = _as_int(meta.get("commit_count_90d"))
        if commits_90d < _MIN_COMMITS_90D:
            return []

        severity = Severity.HIGH if scatter >= _HIGH_SCATTER else Severity.MEDIUM

        return [
            BiomarkerResult(
                biomarker_type=self.name,
                severity=severity,
                function_name=None,
                line_start=None,
                line_end=None,
                details={
                    "scatter": scatter,
                    "commit_count_90d": commits_90d,
                },
                reason=(
                    f"co-changes with {scatter} distinct files — editing this "
                    "file tends to ripple across the codebase (shotgun surgery)"
                ),
            )
        ]


BIOMARKER = CoChangeScatterDetector()
