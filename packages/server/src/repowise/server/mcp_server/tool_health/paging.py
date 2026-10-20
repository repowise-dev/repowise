"""Collection accounting for get_health: every emitted list states its total.

``Pager`` bounds each list and remembers how to fetch what it cut: a paged
collection gets a ``recovery`` call, anything else keeps its exact tail in the
shared omission store.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from typing import Any

from repowise.server.mcp_server._budget import OmissionCollector, register_post_enforce
from repowise.server.mcp_server.tool_health.request import PLANS_PAGE_CAP, HealthRequest

_PAGED_COLLECTIONS = frozenset(
    {
        "metrics",
        "findings",
        "trends",
        "worst_files",
        "test_worst_files",
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
_QUEUE_COLLECTIONS = frozenset({"refactoring_opportunities", "performance_opportunities"})
_QUEUE_PAGE = 6

#: Collections of one unit each whose ``<key>_counts.shown`` is the rows that
#: arrived. A plan page's rows are steps, so its count stays the opportunities.
_COUNTED_COLLECTIONS = ("top_findings", "performance_opportunities")

#: A collection's fixed page, for naming a cut below ``limit`` a cap.
_COLLECTION_CAPS = {"refactoring_plans": PLANS_PAGE_CAP, "performance_opportunities": _QUEUE_PAGE}


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

    def note_page(self, label: str, *, start: int, shown: int, total: int, limit: int) -> None:
        """A page of ``total`` rows from ``start`` that holds ``shown``: the next
        page's recovery, when there is one."""
        if limit and start + shown < total:
            self.recoveries[label] = (start + shown, max(shown, 1), total - start - shown)

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
                "call": call_for(req, root, limit=next_limit, cursor=next_cursor),
            }
        return recovery or None

    def report_omissions(self, result: dict[str, Any], collector: OmissionCollector) -> None:
        """Hand every dropped tail whose block survived to the omission store."""
        for label, dropped in self.omissions.items():
            root = label.split(".", 1)[0]
            if root in result:
                collector.add(
                    f"{label} beyond emitted cap ({len(dropped)} dropped)",
                    [_omitted_row(row) for row in dropped],
                )


#: Every queue argument the request takes except the view, which every call names.
_QUEUE_FILTERS = tuple(
    f.name
    for f in fields(HealthRequest)
    if f.init
    and f.name.startswith(("refactoring_", "performance_"))
    and f.name != "refactoring_view"
)


def call_for(req: HealthRequest, root: str, *, limit: int, cursor: int) -> str:
    """The call that reads ``root`` again from ``cursor``, under the same
    arguments and queue filters, so the next page reads the same queue."""
    filters = "".join(
        f", {name}={value!r}" for name in _QUEUE_FILTERS if (value := getattr(req, name)) is not None
    )
    return (
        f"get_health(targets={req.raw_targets!r}, include={list(req.include or [])!r}, "
        f"repo={req.repo!r}, limit={limit}, only={[root]!r}, "
        f"refactoring_view='{req.refactoring_view}'{filters}, cursor={cursor})"
    )


def _settle_pages(result: dict[str, Any], call: Mapping[str, Any]) -> None:
    """Restate each named page from what the response budget delivered.

    A page's next cursor is set before the budget runs; a trimmed tail would
    otherwise be skipped by the next call, and ``counts.shown`` would count
    rows that never arrived.
    """
    names = [f.name for f in fields(HealthRequest) if f.init]
    if any(name not in call for name in names):
        return  # not get_health's own signature: nothing to restate the call from
    req = HealthRequest(**{name: call[name] for name in names})
    pager = Pager(req.limit, req.cursor)
    block = result.get("fix_first")
    if isinstance(block, dict) and isinstance(block.get("items"), list):
        shown = len(block["items"])
        if isinstance(block.get("counts"), dict):
            block["counts"]["shown"] = shown
        if req.pages_fix_first:
            total = int(block.get("items_total") or 0)
            pager.note_page(
                "fix_first",
                start=req.fix_first_cursor,
                shown=shown,
                total=total,
                limit=req.fix_first_cap,
            )
            _restate(result, pager, req, "fix_first")
    for key in _COUNTED_COLLECTIONS:
        counts = result.get(f"{key}_counts")
        if isinstance(counts, dict) and isinstance(result.get(key), list):
            counts["shown"] = len(result[key])
    plans = result.get("refactoring_plans")
    if isinstance(plans, list) and "refactoring_plans_total" in result:
        total = int(result["refactoring_plans_total"] or 0)
        pager.note_page(
            "refactoring_plans",
            start=req.cursor,
            shown=len(plans),
            total=total,
            limit=req.plans_cap,
        )
        _restate(result, pager, req, "refactoring_plans")


def _restate(result: dict[str, Any], pager: Pager, req: HealthRequest, label: str) -> None:
    """Replace ``label``'s recovery with the one ``pager`` holds, or drop it."""
    recovery = {key: value for key, value in (result.get("recovery") or {}).items() if key != label}
    if label in pager.recoveries:
        recovery[label] = (pager.recovery_block(result, req) or {})[label]
    if recovery:
        result["recovery"] = recovery
    else:
        result.pop("recovery", None)


register_post_enforce("get_health", _settle_pages, with_call=True)


def _omitted_row(row: Any) -> Any:
    return row.as_dict() if hasattr(row, "as_dict") else row


def _stamp_collection_totals(
    result: dict[str, Any], totals: dict[str, int | None], limit: int
) -> None:
    """Stamp each top-level collection with the population it was cut from."""
    for key, total in totals.items():
        if total is None:
            continue
        cap = _COLLECTION_CAPS.get(key)
        cap_reason = (
            "collection_cap"
            if cap is not None and limit > cap and len(result.get(key, [])) == cap
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
        # A next call's keyword arguments: a count stamped there becomes an argument.
        if key == "arguments":
            continue
        if isinstance(child, list):
            total_key = f"{key}_total"
            emitted_key = f"{key}_emitted"
            total = int(value.get(total_key, len(child)) or 0)
            value.setdefault(total_key, total)
            value.setdefault(emitted_key, len(child))
            if len(child) < total:
                value.setdefault(f"{key}_reduced_reason", "limit")
        _stamp_nested_collections(child)
