"""Public dataclasses + enum for dead-code findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .risk_factors import RISK_CAP_CONFIDENCE, SAFE_CONFIDENCE_THRESHOLD


class DeadCodeKind(StrEnum):
    UNREACHABLE_FILE = "unreachable_file"
    UNUSED_EXPORT = "unused_export"
    UNUSED_INTERNAL = "unused_internal"
    ZOMBIE_PACKAGE = "zombie_package"


@dataclass
class DeadCodeFindingData:
    kind: DeadCodeKind
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    last_commit_at: datetime | None
    commit_count_90d: int
    # Physical lines the finding covers; ``None`` when it could not be
    # counted (the evidence says why) — never an estimate.
    lines: int | None
    evidence: list[str]
    safe_to_delete: bool
    primary_owner: str | None
    age_days: int | None
    # Runtime-load risk factors (config / bootstrap / database / environment /
    # script). Non-empty means the finding is a review candidate, never
    # deletion-ready, regardless of confidence. See :mod:`risk_factors`.
    risk_factors: list[str] = field(default_factory=list)
    # Symbol span in the defining file, when known (symbol-kind findings
    # only; file/package-kind findings leave both None).
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class DeadCodeReport:
    repo_id: str
    analyzed_at: datetime
    total_findings: int
    findings: list[DeadCodeFindingData]
    deletable_lines: int
    confidence_summary: dict  # {"high": N, "medium": N, "low": N}
    #: Number of findings produced by the analyzers but dropped because their
    #: confidence is below ``min_confidence``.  Exposed so the CLI can print a
    #: "N findings hidden; pass --min-confidence 0.0 to see them" footer
    #: without changing what the buckets mean or what the report returns.
    hidden_below_threshold: int = 0
    #: The file paths this report is allowed to speak for, or ``None`` for
    #: "all of them". Confidence is scored from per-file git metadata, and a
    #: file with no metadata is indistinguishable from one with no commits —
    #: it scores 0.7 with ``safe_to_delete=True`` however active it is. The
    #: incremental path can only obtain metadata for some files, so it sets
    #: this to the set it actually has, and persistence leaves every other
    #: file's stored verdict alone rather than overwriting it with a guess.
    #: A full run has metadata for everything and leaves this ``None``.
    authoritative_paths: frozenset[str] | None = None

    @classmethod
    def from_findings(
        cls, findings: list[DeadCodeFindingData], *, hidden_below_threshold: int = 0
    ) -> DeadCodeReport:
        """The report over *findings*, already cut at the confidence floor."""
        high = sum(1 for f in findings if f.confidence >= SAFE_CONFIDENCE_THRESHOLD)
        low = sum(1 for f in findings if f.confidence < RISK_CAP_CONFIDENCE)
        return cls(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            total_findings=len(findings),
            findings=findings,
            # Sum of the known counts: a lower bound when any count is unknown.
            deletable_lines=sum(f.lines or 0 for f in findings if f.safe_to_delete),
            confidence_summary={"high": high, "medium": len(findings) - high - low, "low": low},
            hidden_below_threshold=hidden_below_threshold,
        )
