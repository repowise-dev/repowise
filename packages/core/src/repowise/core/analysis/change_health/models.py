"""Typed vocabulary for a base-versus-head health comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DeltaStatus = Literal[
    "available",
    "partial",
    "unavailable",
    "unsupported_range",
    "too_large",
    "timeout",
    "analyzer_mismatch",
    "rules_mismatch",
    "stale_baseline",
]

ChangeKind = Literal["introduced", "worsened", "unchanged", "resolved", "uncertain"]

#: How a head-side finding is tied to the change. Ordered most to least direct;
#: ``unknown`` never claims the change caused the finding.
AttributionBasis = Literal[
    "added_lines",
    "changed_symbol",
    "changed_call_edge",
    "new_file",
    "file_change",
    "context_change",
    "unknown",
]

ATTRIBUTION_CONFIDENCE: dict[str, str] = {
    "added_lines": "high",
    "changed_symbol": "high",
    "new_file": "high",
    "changed_call_edge": "medium",
    "file_change": "medium",
    "context_change": "low",
    "unknown": "low",
}

SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

#: Why a changed file produced no comparison. Kept per file so a partial run
#: can never render as a clean one.
SkipReason = Literal[
    "unsupported_language",
    "generated",
    "binary",
    "excluded",
    "deleted",
    "unreadable",
    "parse_failed",
    "too_large",
]


@dataclass(frozen=True, slots=True)
class RevisionId:
    """What was actually analysed on one side of the comparison."""

    ref: str
    sha: str
    kind: Literal["commit", "working_tree"]

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha": self.sha, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class AnalysisFingerprint:
    """Identity both sides must share for a comparison to mean anything."""

    analyzer_version: int
    rules_fingerprint: str
    performance_model_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzer_version": self.analyzer_version,
            "rules_fingerprint": self.rules_fingerprint,
            "performance_model_version": self.performance_model_version,
        }


@dataclass(frozen=True, slots=True)
class FindingKey:
    """Tolerant semantic identity for one finding.

    Deliberately free of line numbers and storage ids: a finding that moves
    with its symbol is the same finding. Multiplicity within one key is
    resolved by line proximity at match time, never by identity.
    """

    dimension: str
    biomarker_type: str
    path: str
    symbol: str | None

    def as_tuple(self) -> tuple[str, str, str, str | None]:
        return (self.dimension, self.biomarker_type, self.path, self.symbol)


@dataclass(slots=True)
class ChangeFinding:
    """One finding the change introduced or worsened, and why it is charged here."""

    change_finding_id: str
    change_kind: ChangeKind
    dimension: str
    biomarker_type: str
    severity: str
    path: str
    symbol: str | None
    line_start: int | None
    line_end: int | None
    reason: str
    attribution_basis: AttributionBasis
    attribution_confidence: str
    attribution_detail: str
    suggestion: str
    follow_up: str
    severity_before: str | None = None
    health_impact: float = 0.0
    #: Performance only: the causal opportunity this finding belongs to.
    opportunity_id: str | None = None
    opportunity_rank: int | None = None
    #: Canonical typed reference, only when the head finding exists in storage.
    health_reference: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScopeCounts:
    """How much of the change the comparison actually covered."""

    changed: int = 0
    eligible: int = 0
    analyzed: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "changed": self.changed,
            "eligible": self.eligible,
            "analyzed": self.analyzed,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass(slots=True)
class ChangeHealthDelta:
    """The full comparison result, before any response-shaping."""

    status: DeltaStatus
    explanation: str
    base: RevisionId | None
    head: RevisionId | None
    comparison_basis: str
    fingerprint: AnalysisFingerprint | None
    scope: ScopeCounts = field(default_factory=ScopeCounts)
    findings: list[ChangeFinding] = field(default_factory=list)
    resolved_total: int = 0
    unchanged_total: int = 0
    uncertain_total: int = 0
    #: ``{path: reason}``; a non-empty map means the run was partial.
    skipped: dict[str, str] = field(default_factory=dict)
    timing_ms: float = 0.0
    cache_hit: bool = False
    limits: list[str] = field(default_factory=list)

    @property
    def introduced_total(self) -> int:
        return sum(1 for f in self.findings if f.change_kind == "introduced")

    @property
    def worsened_total(self) -> int:
        return sum(1 for f in self.findings if f.change_kind == "worsened")

    @property
    def is_clean(self) -> bool:
        """True only when a valid comparison surfaced nothing new."""
        return self.status == "available" and not self.findings
