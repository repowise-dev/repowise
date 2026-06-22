"""Git-intelligence response models."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel


class GitMetadataResponse(BaseModel):
    file_path: str
    commit_count_total: int
    commit_count_90d: int
    commit_count_30d: int
    first_commit_at: datetime | None
    last_commit_at: datetime | None
    primary_owner_name: str | None
    primary_owner_email: str | None
    primary_owner_commit_pct: float | None
    recent_owner_name: str | None
    recent_owner_commit_pct: float | None
    top_authors: list[dict]
    significant_commits: list[dict]
    co_change_partners: list[dict]
    is_hotspot: bool
    is_stable: bool
    churn_percentile: float
    age_days: int
    bus_factor: int
    contributor_count: int
    lines_added_90d: int
    lines_deleted_90d: int
    avg_commit_size: float
    commit_categories: dict
    merge_commit_count_90d: int
    # Change-complexity + defect-history signals (computed every index, newly
    # surfaced). change_entropy_pct is normalized 0-100 like churn_percentile.
    change_entropy: float = 0.0
    change_entropy_pct: float = 0.0
    prior_defect_count: int = 0
    temporal_hotspot_score: float | None = None
    commit_count_capped: bool = False
    # Rename lineage: the file's path before its most recent move, if any.
    original_path: str | None = None
    test_gap: bool | None = None
    # Agent-provenance rollup: share of the file's indexed commits that are
    # agent-attributed (deterministic local-git channels), with the autonomy-
    # tier breakdown ({"1": n, "2": n, "3": n}). agent_authored_pct is None
    # for rows persisted before the provenance-aware index ran.
    agent_commit_count: int = 0
    agent_authored_pct: float | None = None
    agent_tier_counts: dict = {}

    @classmethod
    def from_orm(cls, obj: object) -> GitMetadataResponse:
        return cls(
            file_path=obj.file_path,  # type: ignore[attr-defined]
            commit_count_total=obj.commit_count_total,  # type: ignore[attr-defined]
            commit_count_90d=obj.commit_count_90d,  # type: ignore[attr-defined]
            commit_count_30d=obj.commit_count_30d,  # type: ignore[attr-defined]
            first_commit_at=obj.first_commit_at,  # type: ignore[attr-defined]
            last_commit_at=obj.last_commit_at,  # type: ignore[attr-defined]
            primary_owner_name=obj.primary_owner_name,  # type: ignore[attr-defined]
            primary_owner_email=obj.primary_owner_email,  # type: ignore[attr-defined]
            primary_owner_commit_pct=obj.primary_owner_commit_pct,  # type: ignore[attr-defined]
            recent_owner_name=obj.recent_owner_name,  # type: ignore[attr-defined]
            recent_owner_commit_pct=obj.recent_owner_commit_pct,  # type: ignore[attr-defined]
            top_authors=json.loads(obj.top_authors_json),  # type: ignore[attr-defined]
            significant_commits=json.loads(obj.significant_commits_json),  # type: ignore[attr-defined]
            co_change_partners=json.loads(obj.co_change_partners_json),  # type: ignore[attr-defined]
            is_hotspot=obj.is_hotspot,  # type: ignore[attr-defined]
            is_stable=obj.is_stable,  # type: ignore[attr-defined]
            # Normalize 0-1 -> 0-100 to match the rest of the HTTP API.
            churn_percentile=(obj.churn_percentile or 0.0) * 100.0,  # type: ignore[attr-defined]
            age_days=obj.age_days,  # type: ignore[attr-defined]
            bus_factor=obj.bus_factor or 0,  # type: ignore[attr-defined]
            contributor_count=obj.contributor_count or 0,  # type: ignore[attr-defined]
            lines_added_90d=obj.lines_added_90d or 0,  # type: ignore[attr-defined]
            lines_deleted_90d=obj.lines_deleted_90d or 0,  # type: ignore[attr-defined]
            avg_commit_size=obj.avg_commit_size or 0.0,  # type: ignore[attr-defined]
            commit_categories=json.loads(obj.commit_categories_json)
            if obj.commit_categories_json
            else {},  # type: ignore[attr-defined]
            merge_commit_count_90d=obj.merge_commit_count_90d or 0,  # type: ignore[attr-defined]
            change_entropy=obj.change_entropy or 0.0,  # type: ignore[attr-defined]
            # Normalize 0-1 -> 0-100 to match churn_percentile's contract.
            change_entropy_pct=(obj.change_entropy_pct or 0.0) * 100.0,  # type: ignore[attr-defined]
            prior_defect_count=obj.prior_defect_count or 0,  # type: ignore[attr-defined]
            temporal_hotspot_score=obj.temporal_hotspot_score,  # type: ignore[attr-defined]
            commit_count_capped=bool(obj.commit_count_capped),  # type: ignore[attr-defined]
            original_path=obj.original_path,  # type: ignore[attr-defined]
            agent_commit_count=getattr(obj, "agent_commit_count", 0) or 0,
            agent_authored_pct=getattr(obj, "agent_authored_pct", None),
            agent_tier_counts=json.loads(getattr(obj, "agent_tier_counts_json", None) or "{}"),
        )


class HotspotResponse(BaseModel):
    file_path: str
    commit_count_total: int = 0
    commit_count_90d: int
    commit_count_30d: int
    churn_percentile: float
    temporal_hotspot_score: float | None = None
    primary_owner: str | None
    primary_owner_commit_pct: float | None = None
    recent_owner_name: str | None = None
    recent_owner_commit_pct: float | None = None
    is_hotspot: bool
    is_stable: bool
    bus_factor: int
    contributor_count: int
    lines_added_90d: int
    lines_deleted_90d: int
    avg_commit_size: float
    commit_categories: dict
    merge_commit_count_90d: int = 0
    commit_count_capped: bool = False
    age_days: int = 0
    last_commit_at: datetime | None = None
    # Change-complexity + defect-history signals.
    change_entropy: float = 0.0
    change_entropy_pct: float = 0.0
    prior_defect_count: int = 0
    original_path: str | None = None


class OwnershipEntry(BaseModel):
    module_path: str
    primary_owner: str | None
    owner_pct: float | None
    file_count: int
    is_silo: bool


class GitSummaryResponse(BaseModel):
    total_files: int
    hotspot_count: int
    stable_count: int
    average_churn_percentile: float
    top_owners: list[dict]


class CommitResponse(BaseModel):
    """One per-commit row from the ``git_commits`` table.

    Carries the stored raw change-risk plus a **repo-relative** normalization
    (``risk_percentile`` + ``review_priority``). The raw ``change_risk_level``
    is the absolute calibration band — kept for transparency but de-emphasized
    in the UI, because it skews high on repos with large typical commits. The
    review-priority queue ranks on ``risk_percentile`` instead.
    """

    sha: str
    short_sha: str
    author_name: str
    author_email: str
    committed_at: datetime | None
    subject: str
    lines_added: int
    lines_deleted: int
    files_changed: int
    dirs_changed: int
    subsystems_changed: int
    entropy: float
    is_fix: bool
    change_risk_score: float | None
    change_risk_level: str | None
    # Repo-relative normalization (the portable signal).
    risk_percentile: float
    review_priority: str
    # The dominant risk driver, surfaced on rows so reviewers don't have to
    # open every detail sheet. Recomputed deterministically from the stored
    # Kamei features; None when the commit was never risk-scored.
    top_driver: str | None = None
    # Cumulative prior-commit count at commit time. Low values flag a
    # new-to-this-repo contributor.
    author_experience: int | None = None
    # Agent provenance (deterministic local-git attribution channels).
    # agent_name is None for human-authored commits.
    agent_name: str | None = None
    agent_autonomy_tier: int | None = None
    agent_confidence: str | None = None


class RiskDriverResponse(BaseModel):
    """One feature's signed contribution to a commit's change-risk logit."""

    feature: str
    value: float | None
    contribution: float
    label: str


class CommitDetailResponse(CommitResponse):
    """A single commit with its full, attributable risk-driver breakdown."""

    drivers: list[RiskDriverResponse] = []
    agent_channel: str | None = None


class AgentTrendBucket(BaseModel):
    """One month of agent-vs-human commit volume."""

    month: str  # "YYYY-MM"
    total_commits: int
    agent_commits: int
    agent_pct: float  # 0-100
    tier_counts: dict = {}


class AgentTrendResponse(BaseModel):
    """Monthly agent-share trend across the indexed commit window."""

    buckets: list[AgentTrendBucket]
    total_commits: int
    agent_commits: int
    agent_pct: float  # 0-100 across the whole window
    agent_names: list[dict] = []  # [{name, count}] descending


class CommitEvolutionBucket(BaseModel):
    """One time bucket of commit-category counts for the Code Evolution timeline.

    ``counts`` keys are drawn from
    :data:`repowise.core.ingestion.git_indexer._constants.EVOLUTION_CATEGORIES`;
    a category is omitted when its count is zero in this bucket.
    """

    period: str  # "YYYY-MM" (monthly) or "YYYY-Wnn" (weekly)
    start: str  # ISO date of the bucket's first day, for axis ordering/tooltips
    total: int
    counts: dict[str, int] = {}


class CommitEvolutionResponse(BaseModel):
    """Commit-category mix over time — the repo's development "story arc".

    Each commit is classified into exactly one category (feature/fix/refactor/
    docs/test/deps/chore/other) from its subject and bucketed by ``granularity``.
    The UI renders ``buckets`` as a stacked area (share or volume).
    """

    buckets: list[CommitEvolutionBucket]
    categories: list[str]  # categories present across the window, in canonical order
    totals: dict[str, int] = {}  # window-wide count per category
    total_commits: int
    granularity: str  # "month" | "week"
    first_commit_at: str | None = None
    last_commit_at: str | None = None


class CommitStatsResponse(BaseModel):
    """Repo-wide commit aggregates for the commits-page headline stat cards.

    Computed over **all** indexed commits, not the loaded page — the paginated
    feed only returns a window, so client-side reductions over it under-count
    (e.g. a risk-sorted first page is all top-tercile). These are the honest
    totals for the whole repository.
    """

    total_commits: int
    high_priority_count: int  # commits in the repo-relative top risk tercile
    fix_commit_count: int  # subjects classified as bug-fixes
    agent_commit_count: int  # agent-attributed commits
    avg_entropy: float  # mean change-diffusion across all commits
