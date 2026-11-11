"""The refactoring board's summary chips, over plain plan rows.

Kept outside the ``refactoring`` package on purpose: importing that package
registers every detector, which a reader that only counts plans should not pay.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from repowise.core.analysis.health.rows import field, json_field

#: Plans an opportunity carries as evidence, never as a step: a cycle names
#: edges to cut but not which symbols cross them.
ADVISORY_TYPES = frozenset({"break_cycle"})

#: The types the board groups under its "Structural" lens. Advisory types are
#: left out so the plan chips agree with the opportunities, where they never
#: lead; ``by_type`` still counts them.
STRUCTURAL_TYPES = frozenset({"split_file", "extract_class", "move_method"})

#: The types whose plan is a set of groups to create. With a group unnamed, the
#: plan says what to separate but not what the result is: a design decision for
#: a person, held out of every opportunity's steps (``needs_design``).
GROUPING_TYPES = frozenset({"split_file", "extract_class"})

#: A plan recovers health at or above this ``impact_delta``.
HEALTH_RECOVERY_MIN = 0.1

#: A plan's health gain is negligible below this ``impact_delta``. Overlaps the
#: recovery band by design: the two chips answer different questions.
NEGLIGIBLE_HEALTH_BELOW = 0.5


def groups_named(plan: dict[str, Any]) -> bool | None:
    """Whether every proposed group is named: the file a split group lands in,
    or the class an extracted group becomes.

    ``None`` when the plan proposes no groups at all: absence of groups is not
    evidence that the naming succeeded, and an unnameable group is emitted as
    ``null`` rather than given an invented name.
    """
    groups = [group for group in (plan.get("groups") or []) if isinstance(group, dict)]
    if not groups:
        return None
    return all(group.get("suggested_file") or group.get("name") for group in groups)


def needs_design(row: Any) -> bool:
    """Whether *row* (a suggestion, ORM row or dict) is a grouping plan with a
    group it does not name."""
    if field(row, "refactoring_type") not in GROUPING_TYPES:
        return False
    plan = field(row, "plan", None)
    if not isinstance(plan, dict):
        plan = json_field(row, "plan_json", {})
    return groups_named(plan if isinstance(plan, dict) else {}) is not True


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
        # Structural plans no opportunity takes as a step, so the structural
        # chip and the opportunities reconcile.
        "design_total": sum(needs_design(plan) for plan in plans),
        "performance_total": by_type.get("performance_fix", 0),
        "small_effort_total": sum(field(plan, "effort_bucket") == "S" for plan in plans),
        "health_recovery_total": sum(impact >= HEALTH_RECOVERY_MIN for impact in impacts),
        "negligible_health_total": sum(impact < NEGLIGIBLE_HEALTH_BELOW for impact in impacts),
        "best_health_gain": round(max((float(impact) for impact in impacts), default=0.0), 3),
    }


__all__ = [
    "ADVISORY_TYPES",
    "GROUPING_TYPES",
    "HEALTH_RECOVERY_MIN",
    "NEGLIGIBLE_HEALTH_BELOW",
    "STRUCTURAL_TYPES",
    "groups_named",
    "needs_design",
    "summarize_plans",
]
