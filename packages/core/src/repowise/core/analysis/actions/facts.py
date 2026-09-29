"""The inputs the action rules read, as plain frozen records.

A loader fills these from the stores; rules only read them. Every store is
optional: a store the loader could not read is named in
:attr:`RepoFacts.unavailable` with a reason, and a rule that needs it reports
itself as not evaluated rather than as clear.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

CoverageStatus = Literal["measured", "stale", "unknown"]


@dataclass(frozen=True, slots=True)
class LeadFinding:
    """A file's strongest code-shape finding: the thing an edit can fix."""

    biomarker: str
    severity: str
    function: str | None
    line: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class FileFacts:
    path: str
    is_test: bool
    score: float | None
    max_ccn: int | None
    nloc: int | None
    line_coverage_pct: float | None
    commits_90d: int
    last_commit_at: datetime | None
    bug_magnet: bool
    fix_commits_90d: int
    bus_factor: int | None
    owner_key: str | None
    owner_name: str | None
    owner_pct: float | None
    dependents: int | None
    lead: LeadFinding | None = None


@dataclass(frozen=True, slots=True)
class RecentFinding:
    """A finding a commit in the recent window introduced or made worse."""

    sha: str
    subject: str
    committed_at: datetime | None
    file_path: str
    symbol: str | None
    biomarker: str
    severity: str
    change_kind: str
    line: int | None


@dataclass(frozen=True, slots=True)
class PerfFacts:
    opportunity_id: str
    biomarker: str
    boundary: str | None
    file_path: str
    symbol: str | None
    call_sites: int
    files: int
    actionability: str
    exposure: str | None
    loop_magnitude: str | None
    effort: str | None


@dataclass(frozen=True, slots=True)
class SecretFacts:
    file_path: str
    kind: str
    line: int | None
    snippet: str


@dataclass(frozen=True, slots=True)
class DriftFacts:
    document: str
    kind: str
    target: str
    #: The link as the document wrote it; ``target`` is normalised against the
    #: document's own path, which turns a site-root `/#install` into a
    #: same-file anchor that was never meant.
    raw: str
    line: int | None
    reason: str
    confidence: float
    #: The target path appears in this repository's own history or tree, so the
    #: document is describing something that was here. See ``rules.docs``.
    target_known: bool


@dataclass(frozen=True, slots=True)
class DeadFacts:
    finding_id: str
    file_path: str
    symbol: str | None
    lines: int


@dataclass(frozen=True, slots=True)
class DecisionFacts:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class CoverageState:
    status: CoverageStatus
    ingested_at: datetime | None = None
    files_measured: int = 0


@dataclass(frozen=True, slots=True)
class RepoFacts:
    """Everything the rules may read about one repository."""

    #: The newest commit the index holds. Windows are measured back from here,
    #: not from the wall clock, so a quiet or archived repository still reads
    #: in terms of its own last week of work.
    anchor: datetime | None
    files: Mapping[str, FileFacts] = field(default_factory=dict)
    #: Distinct bug-fix commit shas in the last 90 days, per file.
    fix_shas_by_file: Mapping[str, frozenset[str]] = field(default_factory=dict)
    fix_commits_90d: int = 0
    recent_findings: tuple[RecentFinding, ...] = ()
    perf: tuple[PerfFacts, ...] = ()
    secrets: tuple[SecretFacts, ...] = ()
    drift: tuple[DriftFacts, ...] = ()
    dead: tuple[DeadFacts, ...] = ()
    stale_decisions: tuple[DecisionFacts, ...] = ()
    proposed_decisions: int = 0
    accepted_decisions: int = 0
    #: Last commit per author identity key, for "their main author has gone quiet".
    author_last_commit: Mapping[str, datetime] = field(default_factory=dict)
    active_authors_90d: int = 0
    coverage: CoverageState = CoverageState("unknown")
    #: Store name -> why it could not be read.
    unavailable: Mapping[str, str] = field(default_factory=dict)
