"""Co-change Scatter — files coupled to many others (shotgun surgery).

D'Ambros et al. found that a file co-changing with a *large number* of
distinct partners is a modest but real defect signal: every edit risks
rippling across the codebase. This is the breadth complement to
``hidden_coupling`` (which flags *specific* undeclared coupled pairs); here we
flag a file coupled to *many* others regardless of whether the links are
declared.

Reads ``git_meta["co_change_partners_json"]`` (the decay-weighted partner list
the git indexer already stores). **scatter** = the number of distinct partners
whose ``co_change_count`` clears the indexer's recording threshold (2.0).

Fires when the file is actively changing and broadly coupled:

- ``scatter`` ≥ 8 (shotgun-surgery territory), AND
- ``commit_count_90d`` ≥ 3.

Tier-aware: when ``co_change_partners_json`` is empty (ESSENTIAL git tier) the
detector emits nothing.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Severity
from .base import BiomarkerResult, FileContext

_MIN_PARTNER_WEIGHT = 2.0
_SCATTER_THRESHOLD = 8
_HIGH_SCATTER = 15
_MIN_COMMITS_90D = 3


def _count_scatter(meta: dict[str, Any]) -> int:
    raw = meta.get("co_change_partners_json")
    if not raw:
        return 0
    try:
        partners = json.loads(raw)
    except (TypeError, ValueError):
        return 0
    scatter = 0
    for p in partners:
        if not isinstance(p, dict):
            continue
        weight = p.get("co_change_count") or p.get("count") or 0
        try:
            if float(weight) >= _MIN_PARTNER_WEIGHT:
                scatter += 1
        except (TypeError, ValueError):
            continue
    return scatter


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
