"""Blast-radius request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from repowise.server.schemas.git import (
    ChangeFeaturesResponse,
    FixHistoryResponse,
    RiskDriverResponse,
)
from repowise.server.schemas.risk_semantics import (
    RiskAuthority,
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
    #: Every changed file this test reaches, where ``source_files`` names the first few.
    source_files_total: int | None = None
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


class ChangeCandidateModel(BaseModel):
    id: str
    #: A ``change_request`` (merge or pull request) is never listed; a link opens one.
    kind: Literal["working_tree", "branch", "commit", "change_request"]
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
    #: For a change request: its number on the forge.
    number: int | None = None
    #: For a change request: the commit it is compared against.
    base_sha: str | None = None
    #: For a change request: what its forge calls one ("merge request", "pull request").
    change_term: str | None = None


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


#: Reached files per page of ``transitive_affected`` when a request names no ``limit``.
CHANGE_PAGE_SIZE = 500
#: The most one page may hold: enough to draw the map of a wide change.
CHANGE_PAGE_MAX = 5000


class ChangeBlastRadiusRequest(BaseModel):
    #: An ``id`` from ``GET /blast-radius/changes``.
    change_id: str = Field(min_length=1, max_length=300)
    max_depth: int = Field(default=3, ge=1, le=10)
    #: Also return ``via`` on each reached file, for drawing the map.
    with_graph: bool = False
    #: The page of ``transitive_affected``: rows ``offset`` to ``offset + limit``.
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=CHANGE_PAGE_SIZE, ge=1, le=CHANGE_PAGE_MAX)


class ReachSummary(BaseModel):
    """Counts over every file a change reaches, not only the page sent."""

    #: Source files the change reaches; tests are counted apart.
    files: int
    #: Test files that import the change at any depth.
    tests: int
    #: Source files one import away.
    direct: int
    #: Modules the reached source files sit in.
    modules: int
    max_depth: int
    #: Changed files something imports directly.
    imported: list[str]


class ChangeRiskBlock(BaseModel):
    """``repowise risk <revspec> --format json`` for the change, field for field.

    ``fix_history`` says how often the touched files needed fixes;
    ``risk_percentile`` ranks the diff's size and spread against the
    repository's recent commits (``score`` is the 0-10 model score it ranks).
    Neither is a probability that the change breaks something.
    """

    ref: str
    #: True when the uncommitted work was scored rather than a commit.
    working_tree: bool
    fix_history: FixHistoryResponse
    risk_authority: RiskAuthority
    score: float
    score_measures: str
    score_unit: str
    risk_percentile: float | None
    review_priority: str | None
    classification: str | None
    #: Absolute model-score band, only when there was no baseline to rank against.
    fallback_band: str | None
    baseline_sample_size: int
    exclude_patterns: list[str]
    is_fix: bool
    features: ChangeFeaturesResponse
    drivers: list[RiskDriverResponse]


class ChangeTestsBlock(BaseModel):
    """The tests the change needs: ``get_change_risk``'s ``impacted_tests``, longer.

    ``run_all`` true means run the whole suite, for ``reasons``; false with an
    empty ``tests_to_run`` means no test needs to run. ``status`` is
    ``selected``, ``run_all``, ``degraded`` (the index lags the checkout and the
    list holds most of the suite), or why no selection was made (``no_index``,
    ``unknown``, ``timeout``, ``busy``, ``config_invalid``, ``too_large``).
    """

    status: str
    run_all: bool
    reasons: list[str]
    reasons_total: int | None = None
    #: ``measured`` (coverage decided), ``inferred`` (the graph or a rule), ``none``.
    basis: str
    map_present: bool | None = None
    #: In run order, up to 200; ``total`` counts them all.
    tests_to_run: list[str]
    #: ``test_id`` when coverage node ids are listed, ``test_file`` otherwise.
    tests_to_run_kind: str | None = None
    total: int
    truncated: bool
    always_run_total: int | None = None
    #: Tests that still run but are counted apart from ``total``: kept by a
    #: conftest on a route, or reaching the change only through imports.
    broadcast_total: int | None = None
    import_closure_total: int | None = None
    summary: str
    #: Per listed test file, what put it in.
    why: dict[str, str] | None = None
    #: Per changed file (first ten), the evidence that decided it.
    basis_by_file: dict[str, str] | None = None
    basis_by_file_total: int | None = None


class ChangeReview(BaseModel):
    """The change's score and tests, from the functions ``repowise risk`` and
    ``get_change_risk`` use, so all three give the same answer."""

    #: What was scored: a commit sha, ``<base>...<branch>``, or ``None`` for
    #: the uncommitted work.
    revspec: str | None
    risk: ChangeRiskBlock
    tests: ChangeTestsBlock
    #: Commands that run ``tests.tests_to_run``, one per runner; empty when the
    #: verdict is to run the whole suite or no runner is known for them.
    test_commands: list[str] = Field(default_factory=list)


class ChangeBlastRadiusResponse(BlastRadiusResponse):
    #: The change analyzed. A merge or pull request asked for by number
    #: (``mr:<n>``, ``pr:<n>``) comes back pinned to the two commits read
    #: (``mr:<n>@<base sha>...<head sha>``), so a link built from it never moves.
    change_id: str
    #: The merge or pull request, as the picker shows one; ``None`` for other kinds.
    change: ChangeCandidateModel | None = None
    #: The paths analyzed, after the cap below.
    changed_files: list[str]
    #: Every path the change touches, before the cap.
    changed_files_total: int
    #: Every reached file. ``transitive_affected`` holds one page of them, and
    #: ``affected_signals`` and ``test_gaps`` cover the changed files and that page.
    transitive_total: int = 0
    #: The ``offset`` of the next page; ``None`` on the last.
    next_offset: int | None = None
    reach_summary: ReachSummary | None = None
    #: Files that usually change with a changed one and are left out, all of
    #: them; ``cochange_warnings`` lists the strongest pairs.
    missing_partners_total: int | None = None
    #: The score and tests; ``None`` when the change could not be read from
    #: git here, with ``review_unavailable`` saying why. The blast radius
    #: above is the evidence behind it.
    review: ChangeReview | None = None
    review_unavailable: str | None = None


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
