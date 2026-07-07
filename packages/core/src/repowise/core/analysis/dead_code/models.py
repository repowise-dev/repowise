"""Public dataclasses + enum for dead-code findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


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
    lines: int
    package: str | None
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
