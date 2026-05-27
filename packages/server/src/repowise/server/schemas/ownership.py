"""Ownership, knowledge-map, module-health and reviewer-suggestion models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    files_owned: int
    hotspots_owned: int
    silo_modules: int  # modules where this owner is >80%
    dead_code_files_owned: int
    dead_code_lines_owned: int
    commit_count_90d: int  # sum of per-file 90d commits attributed to this person
    last_commit_at: datetime | None
    bus_factor_risk_files: int  # files they own where bus_factor <= 1


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


class OwnerProfileResponse(BaseModel):
    key: str
    name: str
    email: str | None

    # Headline metrics
    files_owned: int
    hotspots_owned: int
    silo_modules: int
    dead_code_files_owned: int
    dead_code_lines_owned: int
    commit_count_90d: int
    last_commit_at: datetime | None
    first_commit_at: datetime | None
    bus_factor_risk_files: int

    # 90d activity proxies (approximated from file-level totals weighted by
    # this person's share of commits on each file). They are estimates, not
    # exact per-author diff sizes — we never indexed per-author byte counts.
    lines_added_90d_est: int
    lines_deleted_90d_est: int

    # Breakdowns
    modules: list[OwnerModuleRollup]
    top_files: list[OwnerFileEntry]
    co_authors: list[OwnerCoAuthor]

    # Commit-category mix across files they touch (sum of categories).
    commit_categories: dict


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


class ModuleHealthDetail(ModuleHealthSummary):
    """Single-module deep view with breakdowns."""

    owners: list[ModuleHealthOwner]
    top_hotspots: list[str]
    governing_decisions: list[str]  # decision ids
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
