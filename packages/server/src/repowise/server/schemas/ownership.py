"""Ownership, knowledge-map, module-health and reviewer-suggestion models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: Who an identity is: a person, other automation, or a coding agent.
IdentityKindField = Literal["human", "bot", "agent"]


class KnowledgeMapOwner(BaseModel):
    email: str
    name: str
    files_owned: int
    percentage: float


class KnowledgeMapSilo(BaseModel):
    file_path: str
    owner_email: str
    owner_pct: float


class KnowledgeMapTarget(BaseModel):
    path: str
    pagerank: float
    doc_words: int


class KnowledgeMapResponse(BaseModel):
    top_owners: list[KnowledgeMapOwner]
    knowledge_silos: list[KnowledgeMapSilo]
    onboarding_targets: list[KnowledgeMapTarget]


class OwnerListEntry(BaseModel):
    """One row in the engineering-leader-facing owners directory."""

    key: str  # stable identifier for the URL (email if known, else name)
    name: str
    email: str | None
    kind: IdentityKindField
    files_owned: int
    hotspots_owned: int
    silo_modules: int  # modules where this owner is >80%
    dead_code_files_owned: int
    dead_code_lines_owned: int
    # Distinct commits by this person in the 90 days before HEAD; None when the
    # indexed commits do not cover the window.
    commit_count_90d: int | None
    # A floor of that count when the indexed commits stop inside the window.
    commit_count_90d_at_least: int | None = None
    last_commit_at: datetime | None
    bus_factor_risk_files: int  # files they own whose bus_factor is exactly 1


class OwnerModuleRollup(BaseModel):
    module_path: str
    file_count: int
    hotspot_count: int
    dominant_pct: float  # share of files in module owned by this person


class OwnerFileEntry(BaseModel):
    file_path: str
    commit_count_90d: int
    churn_percentile: float  # 0-100
    bus_factor: int
    is_hotspot: bool
    last_commit_at: datetime | None
    primary_owner_commit_pct: float | None


class OwnerCoAuthor(BaseModel):
    name: str
    email: str | None
    shared_files: int
    co_change_strength: float  # 0-1 fraction


class OwnerAliases(BaseModel):
    """Every display name and canonical email folded into one person."""

    names: list[str]
    emails: list[str]


class OwnerProfileResponse(BaseModel):
    key: str
    name: str
    email: str | None
    kind: IdentityKindField
    aliases: OwnerAliases

    # Headline metrics
    files_owned: int
    hotspots_owned: int
    silo_modules: int
    dead_code_files_owned: int
    dead_code_lines_owned: int
    commit_count_90d: int | None
    commit_count_90d_at_least: int | None = None
    last_commit_at: datetime | None
    first_commit_at: datetime | None
    bus_factor_risk_files: int

    # 90d activity proxies (approximated from file-level totals weighted by
    # this person's share of commits on each file). They are estimates, not
    # exact per-author diff sizes — we never indexed per-author byte counts.
    lines_added_90d_est: int
    lines_deleted_90d_est: int

    # Breakdowns. top_files / co_authors are capped server-side; the
    # *_total fields carry the uncapped counts so the UI can render honest
    # "+N more" indicators instead of implying the list is complete.
    modules: list[OwnerModuleRollup]
    top_files: list[OwnerFileEntry]
    files_touched_total: int = 0
    co_authors: list[OwnerCoAuthor]
    co_authors_total: int = 0

    # Commit-category mix across files they touch (sum of categories).
    commit_categories: dict


class OwnerRef(BaseModel):
    key: str
    name: str


class ConcentrationOwner(OwnerRef):
    files_owned: int
    share: float  # of all person-owned files (0-1)


class OwnershipConcentration(BaseModel):
    top_n: int
    # Share of person-owned files the top_n people own; None when nobody owns one.
    share: float | None
    owners: list[ConcentrationOwner]


class SiloModule(BaseModel):
    module: str
    owner: OwnerRef
    owned_files: int
    file_count: int
    share: float  # owned_files / file_count, above 0.8


class QuietOwner(OwnerRef):
    last_commit_at: datetime | None
    files_owned: int
    changing_files_owned: int  # owned files with a commit in the last 90 days


class OwnersSummaryResponse(BaseModel):
    """Repository knowledge risk, people only (automation owns nothing)."""

    truck_factor: int | None
    people: int
    automation_identities: int
    owners: int
    tracked_files: int
    owned_files: int
    single_owner_files: int  # bus factor exactly 1
    single_owner_hotspots: int
    concentration: OwnershipConcentration
    silo_modules: list[SiloModule]
    activity_window_days: int
    # People with a commit in the window; a floor when activity_complete is false.
    active_contributors: int
    activity_complete: bool
    # People with no commit in the window who still own files that changed in it.
    quiet_owners: list[QuietOwner]
    quiet_owners_total: int


class KnowledgeMatrixAuthor(BaseModel):
    key: str  # the owner key used by /owners/{key}
    name: str
    email: str | None
    last_commit_at: str | None
    active: bool  # committed in the 90 days before HEAD


#: One file: (dir index, file name, nloc, health score, is_hotspot, bus_factor,
#: primary owner author index, [(author index, share of human commits)],
#: changed in the last 90 days).
KnowledgeMatrixFile = tuple[
    int, str, int, float | None, bool, int, int | None, list[tuple[int, float]], bool
]


class KnowledgeMatrixResponse(BaseModel):
    """Every code file's top-5 human authors, positional and indexed for size."""

    authors: list[KnowledgeMatrixAuthor]
    dirs: list[str]  # "" is the repository root
    files: list[KnowledgeMatrixFile]


class ModuleHealthOwner(BaseModel):
    name: str
    email: str | None
    file_count: int
    pct: float


class ModuleHealthSummary(BaseModel):
    """One row in the per-module health rollup."""

    module_path: str
    file_count: int
    symbol_count: int
    hotspot_count: int
    dead_code_count: int
    dead_code_lines: int
    avg_churn_percentile: float  # 0-100
    median_bus_factor: float
    min_bus_factor: int
    primary_owner: str | None
    primary_owner_pct: float
    is_silo: bool
    decision_count: int
    doc_coverage_pct: float
    # Composite 0-100 - higher = healthier (high docs, distributed ownership,
    # low dead code, manageable churn). Used to rank modules at a glance.
    health_score: float


class GoverningDecisionRef(BaseModel):
    """Slim decision reference so lists can render titles, not UUIDs."""

    id: str
    title: str
    status: str


class ModuleHealthDetail(ModuleHealthSummary):
    """Single-module deep view with breakdowns."""

    owners: list[ModuleHealthOwner]
    top_hotspots: list[str]
    governing_decisions: list[GoverningDecisionRef]
    contributor_count: int


class ReviewerSuggestion(BaseModel):
    name: str
    email: str | None
    score: float  # 0-1 composite
    recent_commits: int  # commits in the last 90d across the requested paths
    owned_paths: list[str]
    co_change_paths: list[str]
    reasons: list[str]


class ReviewerSuggestionsResponse(BaseModel):
    paths: list[str]
    suggestions: list[ReviewerSuggestion]
