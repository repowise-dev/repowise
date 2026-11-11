"""Blast-radius request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from repowise.server.schemas.risk_semantics import (
    RiskCompatibilityField,
    RiskScalarSemantics,
)


class BlastRadiusRequest(BaseModel):
    changed_files: list[str]
    max_depth: int = Field(default=3, ge=1, le=10)
    #: Also return ``via`` on transitive rows and ``affected_signals``, for drawing.
    with_graph: bool = False


class DirectRiskEntry(BaseModel):
    path: str
    structural_score: float
    #: Deprecated exact alias of ``structural_score`` for older clients.
    risk_score: float
    temporal_hotspot: float
    #: Repo-relative rank of ``temporal_hotspot``; comparable as a rank.
    churn_percentile: float
    #: The index's hotspot verdict: top-quartile churn AND its activity floors.
    is_hotspot: bool
    centrality: float


class TransitiveEntry(BaseModel):
    path: str
    depth: int
    #: The file one hop nearer the change that this one imports. Only sent
    #: with the change analysis, which draws the reach as a graph.
    via: str | None = None


class CochangeWarning(BaseModel):
    changed: str
    missing_partner: str
    score: float


class ReviewerEntry(BaseModel):
    email: str
    files: int
    ownership_pct: float
    #: Display name from git history; only on the change analysis.
    name: str | None = None


class TestImpactEvidence(BaseModel):
    basis: Literal["measured", "inferred"]
    source_file: str
    via: Literal["coverage-map", "call-graph", "import-graph"]
    #: ``file`` on measured evidence: a test ran the changed file, not
    #: necessarily its changed lines.
    match: Literal["file"] | None = None
    source_format: str | None = None


class TestRecommendation(BaseModel):
    test_id: str
    test_file: str | None = None
    repository_id: str
    repository: str
    basis: Literal["measured", "inferred"]
    bases: list[Literal["measured", "inferred"]]
    source_files: list[str]
    evidence: list[TestImpactEvidence]
    #: A measured row whose test file is gone or changed since the coverage run.
    stale: bool = False
    stale_reason: Literal["test_file_missing", "test_file_changed"] | None = None


class TestImpactFile(BaseModel):
    source_file: str
    status: Literal["measured", "inferred", "unknown", "deleted"]
    #: The path's transition, when the caller supplied one.
    change_status: str | None = None
    #: False for a path the change deletes: it has no head side to cover.
    head_present: bool = True
    measured_tests: list[str]
    measured_tests_total: int
    inferred_tests: list[str]
    inferred_tests_total: int


class TestImpactFreshness(BaseModel):
    status: Literal["current", "stale", "unknown"]
    reason: str | None = None
    ingested_commit: str | None = None
    indexed_commit: str | None = None


class TestImpactAggregate(BaseModel):
    file_count: int
    ingested_commit: str | None = None
    freshness: Literal["current", "stale", "unknown"]


class TestImpactCoverage(BaseModel):
    status: Literal["available", "partial", "unavailable", "degraded"]
    reason: str | None = None
    map_present: bool
    pair_count: int
    test_count: int
    source_file_count: int
    changed_files_total: int
    changed_files_with_measured_tests: int
    changed_files_without_measured_tests: int
    ingested_at: str | None = None
    source_format: str | None = None
    freshness: TestImpactFreshness
    #: Stored per-file line coverage, when there is no per-test map.
    aggregate: TestImpactAggregate | None = None


class TestImpactInference(BaseModel):
    status: Literal["available", "degraded"]
    reason: str | None = None
    changed_files_total: int
    changed_files_with_candidates: int
    candidates_before_dedup: int


class TestImpactAnalysis(BaseModel):
    status: Literal["available", "partial", "degraded"]
    stale: bool
    partial: bool
    degraded: bool
    basis_categories: list[Literal["measured", "inferred"]]


class TestImpactResponse(BaseModel):
    recommendations: list[TestRecommendation]
    recommendations_total: int
    recommendations_emitted: int
    recommendations_truncated: bool
    recommendations_omitted: int
    recommendations_by_primary_basis: dict[Literal["measured", "inferred"], int]
    files: list[TestImpactFile]
    files_total: int
    files_without_measured_tests: list[str]
    unknown_files: list[str]
    #: Changed paths the change deletes. Not a coverage gap.
    deleted_files: list[str] = []
    coverage: TestImpactCoverage
    inference: TestImpactInference
    analysis: TestImpactAnalysis


class AffectedSignal(BaseModel):
    """What the index knows about one affected file. ``None`` means unknown, never healthy."""

    path: str
    is_hotspot: bool | None = None
    health_score: float | None = None
    module: str | None = None
    #: A test file: counted as a test to run, never as code the change reaches.
    is_test: bool = False
    #: Whether the index has the file. A file new since the last index run has
    #: no graph, health or owner yet; ``None`` from a server that does not say.
    indexed: bool | None = None


class ChangedOwner(BaseModel):
    """Who owns some of the changed files, by primary authorship."""

    email: str
    name: str | None = None
    #: Changed files this person is the primary owner of.
    files: int


class BlastRadiusResponse(BaseModel):
    #: Per affected file; only when the request asked ``with_graph``.
    affected_signals: list[AffectedSignal] | None = None
    #: Owners of the changed files themselves; only with ``with_graph``.
    changed_owners: list[ChangedOwner] | None = None
    #: Commands that run the recommended tests, one per runner; only with ``with_graph``.
    test_commands: list[str] | None = None
    direct_risks: list[DirectRiskEntry]
    transitive_affected: list[TransitiveEntry]
    cochange_warnings: list[CochangeWarning]
    recommended_reviewers: list[ReviewerEntry]
    test_gaps: list[str]
    test_impact: TestImpactResponse
    structural_impact_score: float
    structural_impact_band: Literal["localized", "moderate", "broad"]
    structural_impact_scale: RiskScalarSemantics
    #: Deprecated exact alias; never repurposed to a new unit.
    overall_risk_score: float
    overall_risk_score_compatibility: RiskCompatibilityField


class PrReviewSummary(BaseModel):
    """What the PR bot stored for a pull request: code health before and after, and new findings.

    A host that runs the bot fills it; a local server never does.
    """

    health_before: float | None = None
    health_after: float | None = None
    #: Findings the pull request introduced.
    introduced: int | None = None
    #: The bot's review of the pull request.
    url: str | None = None


class ChangeCandidateModel(BaseModel):
    id: str
    #: ``pr`` is listed by a host that knows pull requests; a local server never lists one.
    kind: Literal["working_tree", "branch", "commit", "pull_request"]
    label: str
    title: str | None = None
    author: str | None = None
    committed_at: str | None = None
    head_sha: str | None = None
    base: str | None = None
    ahead: int | None = None
    files_changed: int | None = None
    is_current: bool = False
    #: For a branch: whether it exists on the remote. A local-only branch is
    #: often scratch work, so the picker shows it quieter.
    pushed: bool | None = None
    #: For a pull request: its number.
    number: int | None = None
    #: For a pull request: the base commit it is compared against.
    base_sha: str | None = None
    #: For a pull request: the PR bot's stored health before and after, when a host has one.
    pr_review: PrReviewSummary | None = None


class ChangeCandidatesResponse(BaseModel):
    """The changes a reviewer can pick, and the one to open first."""

    changes: list[ChangeCandidateModel]
    default_id: str | None = None
    #: False on a server without a checkout: only indexed commits are offered.
    working_tree_supported: bool
    #: Whether ``POST /blast-radius/health-check`` can judge a listed change.
    #: A local server needs its checkout; a host that reads blobs another way
    #: can say true without a working tree.
    health_check_supported: bool = False


class ChangeBlastRadiusRequest(BaseModel):
    #: An ``id`` from ``GET /blast-radius/changes``.
    change_id: str = Field(min_length=1, max_length=300)
    max_depth: int = Field(default=3, ge=1, le=10)


class ChangeBlastRadiusResponse(BlastRadiusResponse):
    change_id: str
    #: The paths analyzed, after the cap below.
    changed_files: list[str]
    #: Every path the change touches, before the cap.
    changed_files_total: int


class ChangeHealthCheckRequest(BaseModel):
    #: An ``id`` from ``GET /blast-radius/changes``.
    change_id: str = Field(min_length=1, max_length=300)


class ChangeHealthCheckResponse(BaseModel):
    """``repowise health check <revspec> --format json`` for one listed change.

    ``findings`` are the gate's rows (health findings introduced, worsened or
    fixed, and the diff-risk rules); ``gate`` holds the verdict at the CLI
    defaults (fail on introduced, at high).
    """

    change_id: str
    revspec: str
    status: str
    explanation: str | None = None
    scope: dict[str, Any]
    skipped: dict[str, str]
    findings: list[dict[str, Any]]
    gate: dict[str, Any]
