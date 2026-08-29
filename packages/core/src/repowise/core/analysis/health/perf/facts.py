"""Typed, language-neutral facts read off one raw performance finding.

Everything downstream of this module works on :class:`ObservationFacts`, never
on a row. That is what keeps grouping, actionability, and ranking free of the
shape a finding happens to arrive in: an analyzer dataclass mid-run, an ORM row
after persistence, or a plain dict in a fixture all reduce here.

The accessors are deliberately literal about the shipped behaviour, including
two places where the same underlying value is read with different fallbacks
(see :attr:`ObservationFacts.marker` and :attr:`ObservationFacts.evidence_marker`).
Normalising those apart would be a silent output change, not a cleanup.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def field(row: Any, name: str, default: Any = None) -> Any:
    """Read one attribute from a dataclass, an ORM row, or a dict."""
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def detail_map(row: Any) -> dict[str, Any]:
    """The finding's open ``details`` payload, whether stored or in memory."""
    value = field(row, "details", None)
    if isinstance(value, dict):
        return value
    raw = field(row, "details_json", None)
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def is_performance(row: Any) -> bool:
    return field(row, "dimension", None) == "performance"


@dataclass(frozen=True, slots=True)
class ObservationFacts:
    """One detector-supported cost shape at one location.

    ``details`` is kept verbatim because the actionability gates read
    open-ended, marker-specific proof keys off it (``dataflow_verified``,
    ``resource_invariant``, the raw ``path`` list). Closing that dict into typed
    fields would drop the keys a future marker adds.
    """

    finding_id: str
    file_path: str
    function_name: Any
    line_start: Any
    line_end: Any
    reason: str
    raw_marker: Any
    boundary_kind: str | None
    path: tuple[str, ...]
    has_path: bool
    cross_function: bool
    resolution_basis: Any
    reliable_entry_reachability: Any
    details: dict[str, Any]

    @property
    def marker(self) -> str:
        """The identity/ranking view of the biomarker type."""
        return str(self.raw_marker)

    @property
    def evidence_marker(self) -> str:
        """The evidence-row view: a falsy marker renders empty, not ``"None"``."""
        return str(self.raw_marker or "")

    @property
    def provenance(self) -> str:
        """The aggregation view of ``resolution_basis``."""
        return str(self.resolution_basis)

    @property
    def site(self) -> tuple[str, Any, Any]:
        """The call site this observation occupies, for distinct-site counting."""
        return (self.file_path, self.line_start, self.function_name)

    @property
    def sort_key(self) -> tuple[str, Any, str]:
        """Deterministic within-group evidence order."""
        return (self.file_path, self.line_start or 0, str(self.function_name))


def observation_facts(row: Any) -> ObservationFacts:
    details = detail_map(row)
    raw_path = details.get("path", ())
    return ObservationFacts(
        finding_id=str(field(row, "id", "") or ""),
        file_path=str(field(row, "file_path", "")),
        function_name=field(row, "function_name", None),
        line_start=field(row, "line_start", None),
        line_end=field(row, "line_end", None),
        reason=str(field(row, "reason", "") or ""),
        raw_marker=field(row, "biomarker_type", ""),
        boundary_kind=details.get("boundary_kind") or None,
        path=tuple(str(node) for node in raw_path if isinstance(node, str)),
        # Whether a path was *offered*, which is not the same as whether any of
        # it survived the string filter above. Both drive real branches.
        has_path=bool(raw_path),
        cross_function=bool(details.get("cross_function")),
        resolution_basis=details.get("resolution_basis", "direct"),
        reliable_entry_reachability=details.get("reliable_entry_reachability"),
        details=details,
    )


def performance_facts(rows: list[Any]) -> list[ObservationFacts]:
    return [observation_facts(row) for row in rows if is_performance(row)]


def evidence_row(facts: ObservationFacts) -> dict[str, Any]:
    """One public evidence entry.

    ``finding_id`` is the storage row id, not a content hash: empty before
    persistence and a fresh UUID after it. It is a pointer within one index,
    never a cross-reindex key.
    """
    return {
        "finding_id": facts.finding_id,
        "file_path": facts.file_path,
        "biomarker_type": facts.evidence_marker,
        "function_name": facts.function_name,
        "line_start": facts.line_start,
        "line_end": facts.line_end,
        "reason": facts.reason,
        "path": list(facts.details.get("path", ())),
        "provenance": facts.resolution_basis,
    }


__all__ = [
    "ObservationFacts",
    "detail_map",
    "evidence_row",
    "field",
    "is_performance",
    "observation_facts",
    "performance_facts",
]
