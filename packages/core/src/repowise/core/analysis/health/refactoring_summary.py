"""The refactoring board's summary chips, over plain plan rows.

Kept outside the ``refactoring`` package on purpose: importing that package
registers every detector, which a reader that only counts plans should not pay.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from repowise.core.analysis.health.rows import field

#: The types the board groups under its "Structural" lens.
STRUCTURAL_TYPES = frozenset({"split_file", "break_cycle", "extract_class", "move_method"})

#: A plan recovers health at or above this ``impact_delta``.
HEALTH_RECOVERY_MIN = 0.1

#: A plan's health gain is negligible below this ``impact_delta``. Overlaps the
#: recovery band by design: the two chips answer different questions.
NEGLIGIBLE_HEALTH_BELOW = 0.5


def summarize_plans(plans: Iterable[Any]) -> dict[str, Any]:
    """Chip counts for *plans*, each with ``refactoring_type``, ``file_path``,
    ``effort_bucket`` and ``impact_delta``. Shaped like ``RefactoringSummary``."""
    plans = list(plans)
    by_type: dict[str, int] = {}
    for plan in plans:
        kind = field(plan, "refactoring_type")
        by_type[kind] = by_type.get(kind, 0) + 1
    impacts = [field(plan, "impact_delta") for plan in plans]
    return {
        "total": len(plans),
        "by_type": [
            {"type": kind, "count": count}
            for kind, count in sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ],
        "files_total": len({field(plan, "file_path") for plan in plans}),
        "structural_total": sum(
            field(plan, "refactoring_type") in STRUCTURAL_TYPES for plan in plans
        ),
        "performance_total": by_type.get("performance_fix", 0),
        "small_effort_total": sum(field(plan, "effort_bucket") == "S" for plan in plans),
        "health_recovery_total": sum(impact >= HEALTH_RECOVERY_MIN for impact in impacts),
        "negligible_health_total": sum(impact < NEGLIGIBLE_HEALTH_BELOW for impact in impacts),
        "best_health_gain": round(max((float(impact) for impact in impacts), default=0.0), 3),
    }


__all__ = [
    "HEALTH_RECOVERY_MIN",
    "NEGLIGIBLE_HEALTH_BELOW",
    "STRUCTURAL_TYPES",
    "summarize_plans",
]
