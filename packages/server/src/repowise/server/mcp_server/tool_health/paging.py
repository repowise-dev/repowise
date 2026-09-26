"""Collection accounting for get_health: every emitted list states its total.

``Pager`` bounds each list and remembers how to fetch what it cut: a paged
collection gets a ``recovery`` call, anything else keeps its exact tail in the
shared omission store.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.refactoring.recommendations import Recommendation
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_refactoring

_PAGED_COLLECTIONS = frozenset(
    {
        "metrics",
        "findings",
        "trends",
        "worst_files",
        "high_leverage_files",
        "top_findings",
        "test_findings",
        "modules",
        "churn_complexity",
        "coverage.files",
        "doc_drift.findings",
        "refactoring_plans",
        "refactoring_opportunities",
        "refactoring_evidence",
        "performance_opportunities",
        "performance_evidence",
    }
)

# The queues page six at a time whatever ``limit`` says; see the pillar caps.
_QUEUE_COLLECTIONS = frozenset(
    {"refactoring_plans", "refactoring_opportunities", "performance_opportunities"}
)
_QUEUE_PAGE = 6


class Pager:
    """Bounds every emitted collection of one response and records the remainder."""

    def __init__(self, limit: int, cursor: int) -> None:
        self.limit = limit
        self.cursor = cursor
        # ``label -> (next_cursor, next_limit, remaining)`` for paged collections.
        self.recoveries: dict[str, tuple[int, int, int]] = {}
        # ``label -> dropped rows`` for everything else.
        self.omissions: dict[str, list[Any]] = {}

    def bound(self, rows: list[Any], label: str, *, cap: int | None = None) -> list[Any]:
        """Bound one collection and retain its exact tail in shared omission storage."""
        row_cap = self.limit if cap is None else cap
        paged = label in _PAGED_COLLECTIONS
        start = self.cursor if paged else 0
        kept = rows[start : start + row_cap]
        if len(kept) < len(rows):
            if paged:
                self._note_recovery(rows, label, start, start + len(kept), row_cap)
            else:
                self.omissions[label] = rows[row_cap:]
        return kept

    def _note_recovery(
        self, rows: list[Any], label: str, start: int, tail_start: int, row_cap: int
    ) -> None:
        if tail_start < len(rows):
            next_limit = (
                min(row_cap or _QUEUE_PAGE, _QUEUE_PAGE)
                if label in _QUEUE_COLLECTIONS
                else min(len(rows) - tail_start, 50)
            )
            self.recoveries[label] = (tail_start, next_limit, len(rows) - tail_start)
        elif rows and start >= len(rows):
            # A cursor past the end restarts at the first page rather than
            # leaving the caller with nothing to call.
            self.recoveries[label] = (0, min(len(rows), max(row_cap, 1), 50), len(rows))

    def recovery_block(
        self, result: dict[str, Any], req: HealthRequest
    ) -> dict[str, dict[str, Any]] | None:
        """One follow-up call per paged collection that survived the projection."""
        recovery: dict[str, dict[str, Any]] = {}
        for label, (next_cursor, next_limit, remaining) in self.recoveries.items():
            root = label.split(".", 1)[0]
            if root not in result:
                continue
            recovery[label] = {
                "remaining": remaining,
                "call": (
                    f"get_health(targets={req.raw_targets!r}, include={list(req.include or [])!r}, "
                    f"repo={req.repo!r}, limit={next_limit}, only={[root]!r}, "
                    f"refactoring_view='{req.refactoring_view}', cursor={next_cursor})"
                ),
            }
        return recovery or None

    def report_omissions(
        self, result: dict[str, Any], collector: OmissionCollector, reference_repository: str
    ) -> None:
        """Hand every dropped tail whose block survived to the omission store."""
        for label, dropped in self.omissions.items():
            root = label.split(".", 1)[0]
            if root in result:
                collector.add(
                    f"{label} beyond emitted cap ({len(dropped)} dropped)",
                    [_omitted_row(row, reference_repository) for row in dropped],
                )


def _omitted_row(row: Any, reference_repository: str) -> Any:
    if isinstance(row, Recommendation):
        return _serialize_refactoring(row, reference_repository)
    return row.as_dict() if hasattr(row, "as_dict") else row


def _stamp_collection_totals(
    result: dict[str, Any], totals: dict[str, int | None], limit: int
) -> None:
    """Stamp each top-level collection with the population it was cut from."""
    for key, total in totals.items():
        if total is None:
            continue
        cap_reason = (
            "collection_cap"
            if key in {"refactoring_plans", "performance_opportunities"}
            and limit > _QUEUE_PAGE
            and len(result.get(key, [])) == _QUEUE_PAGE
            else "limit"
        )
        _stamp_collection(result, key, total=total, reason=cap_reason)


def _stamp_collection(
    result: dict[str, Any],
    key: str,
    *,
    total: int | None = None,
    reason: str = "limit",
) -> None:
    """Attach complete-population accounting to one emitted collection."""
    rows = result.get(key)
    if not isinstance(rows, list):
        return
    eligible = len(rows) if total is None else total
    emitted = len(rows)
    result[f"{key}_total"] = eligible
    result[f"{key}_emitted"] = emitted
    if emitted < eligible:
        result[f"{key}_reduced_reason"] = reason


def _stamp_nested_collections(value: Any) -> None:
    """Give every nested list an emitted count and an honest eligible total."""
    if isinstance(value, list):
        for item in value:
            _stamp_nested_collections(item)
        return
    if not isinstance(value, dict):
        return
    for key, child in list(value.items()):
        if isinstance(child, list):
            total_key = f"{key}_total"
            emitted_key = f"{key}_emitted"
            total = int(value.get(total_key, len(child)) or 0)
            value.setdefault(total_key, total)
            value.setdefault(emitted_key, len(child))
            if len(child) < total:
                value.setdefault(f"{key}_reduced_reason", "limit")
        _stamp_nested_collections(child)
