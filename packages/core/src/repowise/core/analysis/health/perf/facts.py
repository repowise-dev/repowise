"""Typed, language-neutral facts read off one raw performance finding.

Everything downstream works on :class:`ObservationFacts`, never on a row, so
grouping, actionability, and ranking stay free of the shape a finding arrives
in: analyzer dataclass, ORM row, or plain dict all reduce here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..finding_identity import finding_public_id
from ..rows import detail_map, field


def is_performance(row: Any) -> bool:
    return field(row, "dimension", None) == "performance"


@dataclass(frozen=True, slots=True)
class ObservationFacts:
    """One detector-supported cost shape at one location.

    ``details`` stays verbatim: the actionability gates read marker-specific
    proof keys off it, and closing it into typed fields would drop the keys a
    new marker adds.
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
        """The identity and ranking view of the biomarker type."""
        return str(self.raw_marker)

    @property
    def evidence_marker(self) -> str:
        """The evidence-row view: a falsy marker renders empty, not ``"None"``."""
        return str(self.raw_marker or "")

    @property
    def provenance(self) -> str:
        return str(self.resolution_basis)

    @property
    def terminal_sink(self) -> str | None:
        """The last resolved node on the path, or nothing if none survived.

        ``has_path`` can be true while this is ``None``: a path offered with no
        string nodes names no sink.
        """
        return self.path[-1] if self.path else None

    @property
    def meaningful_predecessor(self) -> str | None:
        """The caller that creates the repetition at the sink.

        The sink is where the cost is paid; its immediate caller is where the
        repetition is introduced, so that caller is the intervention anchor.
        A path with fewer than two resolved nodes names no caller and carries
        no cross-function cause.
        """
        return self.path[-2] if len(self.path) >= 2 else None

    @property
    def path_depth(self) -> int:
        """Resolved hops from the loop-owning function to the sink."""
        return len(self.path)

    @property
    def resource_fingerprint(self) -> str | None:
        """The named API this observation repeats, where a detector recorded one.

        Only the blocking-sync-in-async marker records one today. No detector
        captures argument, query, or path literals, so this is the whole of the
        resource identity currently available.
        """
        value = self.details.get("api")
        return value if isinstance(value, str) and value else None

    @property
    def site(self) -> tuple[str, Any, Any]:
        return (self.file_path, self.line_start, self.function_name)

    @property
    def sort_key(self) -> tuple[str, Any, str]:
        """Deterministic within-group evidence order."""
        return (self.file_path, self.line_start or 0, str(self.function_name))


def observation_facts(row: Any) -> ObservationFacts:
    details = detail_map(row)
    raw_path = details.get("path", ())
    stored = field(row, "public_id", None)
    return ObservationFacts(
        finding_id=stored if isinstance(stored, str) and stored else finding_public_id(row),
        file_path=str(field(row, "file_path", "")),
        function_name=field(row, "function_name", None),
        line_start=field(row, "line_start", None),
        line_end=field(row, "line_end", None),
        reason=str(field(row, "reason", "") or ""),
        raw_marker=field(row, "biomarker_type", ""),
        boundary_kind=details.get("boundary_kind") or None,
        path=tuple(node for node in raw_path if isinstance(node, str) and node),
        # Whether a path was offered, which is not whether any of it survived
        # the string filter above. Both drive real branches.
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

    ``finding_id`` is the finding's public id, which round-trips through the
    ``finding_id`` selector on both surfaces. Storage row ids are republished
    on every analysis and never leave this package.
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
