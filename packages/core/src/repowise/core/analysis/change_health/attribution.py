"""Why a finding is charged to this change, and how sure that is.

Presence at head is never on its own a reason. Every surfaced finding carries
the basis that ties it to the diff, and a finding the diff cannot explain is
reported as such rather than quietly counted as new.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..health import HealthFindingData
from .models import ATTRIBUTION_CONFIDENCE, AttributionBasis
from .sources import FileChange


@dataclass(frozen=True, slots=True)
class Attribution:
    basis: AttributionBasis
    confidence: str
    detail: str


_UNKNOWN = Attribution(
    "unknown",
    ATTRIBUTION_CONFIDENCE["unknown"],
    "Present at head, but the diff does not explain it.",
)


def _overlaps(finding: HealthFindingData, added: set[int]) -> bool:
    start, end = finding.line_start, finding.line_end
    if start is None:
        return False
    return any(line in added for line in range(start, (end or start) + 1))


class FindingAttributor:
    """Decide the strongest basis tying a head finding to the change."""

    def __init__(self, changes: dict[str, FileChange]) -> None:
        self.changes = changes

    def attribute(
        self,
        finding: HealthFindingData,
        *,
        changed_symbols: set[tuple[str, str]] | None = None,
        changed_call_edge: str | None = None,
    ) -> Attribution:
        change = self.changes.get(finding.file_path)
        if change is None:
            return _UNKNOWN
        if change.is_new:
            return Attribution(
                "new_file",
                ATTRIBUTION_CONFIDENCE["new_file"],
                f"{finding.file_path} is added by this change.",
            )
        added = change.added_lines
        if _overlaps(finding, added):
            return Attribution(
                "added_lines",
                ATTRIBUTION_CONFIDENCE["added_lines"],
                f"Lines {finding.line_start}-{finding.line_end or finding.line_start} "
                "are added or rewritten by this change.",
            )
        symbol = finding.function_name
        if symbol and changed_symbols and (finding.file_path, symbol) in changed_symbols:
            return Attribution(
                "changed_symbol",
                ATTRIBUTION_CONFIDENCE["changed_symbol"],
                f"{symbol} is edited by this change.",
            )
        if changed_call_edge:
            return Attribution(
                "changed_call_edge",
                ATTRIBUTION_CONFIDENCE["changed_call_edge"],
                f"Reached through {changed_call_edge}, whose call path this change edits.",
            )
        if added:
            return Attribution(
                "file_change",
                ATTRIBUTION_CONFIDENCE["file_change"],
                f"{finding.file_path} is edited by this change, away from these lines.",
            )
        return Attribution(
            "context_change",
            ATTRIBUTION_CONFIDENCE["context_change"],
            f"{finding.file_path} changed only in ways the diff does not localise.",
        )


def changed_symbols_for(
    changes: dict[str, FileChange], findings_by_file: dict[str, list[HealthFindingData]]
) -> set[tuple[str, str]]:
    """``(path, symbol)`` pairs whose body overlaps the change's added lines."""
    out: set[tuple[str, str]] = set()
    for path, change in changes.items():
        added = change.added_lines
        if not added:
            continue
        for finding in findings_by_file.get(path, ()):
            if finding.function_name and _overlaps(finding, added):
                out.add((path, finding.function_name))
    return out
