"""Workspace (multi-repo) request/response models."""

from __future__ import annotations

from pydantic import BaseModel


class WorkspaceRepoEntry(BaseModel):
    alias: str
    path: str
    is_primary: bool = False
    indexed_at: str | None = None
    last_commit_at_index: str | None = None
    # Per-repo stats (populated from each repo's wiki.db)
    repo_id: str | None = None
    file_count: int = 0
    symbol_count: int = 0
    page_count: int = 0
    doc_coverage_pct: float = 0.0
    hotspot_count: int = 0
    # Canonical 0-100 health score, or None when the repo carries no health
    # metrics. Nullable rather than 0.0 so the UI can tell "not measured"
    # apart from "measured as zero".
    health_score: float | None = None
    # Lifecycle status — surfaced so the web UI can render "needs index"
    # or "missing directory" affordances instead of silently dropping the
    # repo from the sidebar.
    #   "indexed"       — has .repowise/wiki.db with at least one Repository row
    #   "needs_index"   — directory exists but no .repowise/wiki.db yet
    #   "missing_dir"   — workspace config references a path that no longer exists
    status: str = "indexed"
    # Whether docs were generated for this repo. False means a user
    # action ("repowise update --repo <alias> --docs") is required to
    # populate the Docs/Overview tabs in the web UI.
    docs_enabled: bool = True
    # Optional skip reason captured in state.json — surfaced as a
    # transparency hint when docs are disabled.
    docs_skip_reason: str | None = None


class WorkspaceCrossRepoSummary(BaseModel):
    co_change_count: int = 0
    package_dep_count: int = 0
    top_connections: list[dict] = []


class WorkspaceContractSummary(BaseModel):
    total_contracts: int = 0
    total_links: int = 0
    by_type: dict[str, int] = {}


class WorkspaceResponse(BaseModel):
    is_workspace: bool
    workspace_root: str | None = None
    workspace_name: str | None = None
    repos: list[WorkspaceRepoEntry] = []
    default_repo: str | None = None
    cross_repo_summary: WorkspaceCrossRepoSummary | None = None
    contract_summary: WorkspaceContractSummary | None = None


class WorkspaceSyncResult(BaseModel):
    alias: str
    job_id: str | None = None
    repo_id: str | None = None
    status: str  # "accepted", "skipped", "error"
    reason: str | None = None


class WorkspaceSyncResponse(BaseModel):
    results: list[WorkspaceSyncResult]
    accepted: int = 0
    skipped: int = 0
    errors: int = 0


class WorkspaceContractEntry(BaseModel):
    contract_id: str
    contract_type: str
    role: str
    repo: str
    file_path: str
    symbol_name: str
    confidence: float
    service: str | None = None
    #: 1-indexed line of the declaration or call. None when the contract never
    #: bound to a line.
    line: int | None = None
    #: Ingestion symbol id (``"<rel_path>::<name>"``). None when the repo has no
    #: index or nothing is declared at ``line`` — the contract still matches, it
    #: just cannot be traversed into the call graph.
    symbol_id: str | None = None
    #: Extractor-supplied detail (``extraction_layer``, ``framework``, ``method``,
    #: ``path``, ``table``, ``package``...). Keys vary by contract type.
    meta: dict = {}


class WorkspaceContractLinkEntry(BaseModel):
    contract_id: str
    contract_type: str
    match_type: str
    confidence: float
    provider_repo: str
    provider_file: str
    provider_symbol: str
    consumer_repo: str
    consumer_file: str
    consumer_symbol: str
    #: Service boundary the provider sits behind, when the workspace declares
    #: one. Matching skips a pair only when the repo *and* the service are the
    #: same, so this is what explains a link between two services inside one
    #: repo.
    provider_service: str | None = None
    #: The same, for the calling side.
    consumer_service: str | None = None
    #: The linked contracts' symbol ids, so a caller can name the code rather
    #: than a display label. None when that side never bound to one.
    provider_symbol_id: str | None = None
    consumer_symbol_id: str | None = None


class WorkspaceContractsResponse(BaseModel):
    contracts: list[WorkspaceContractEntry]
    links: list[WorkspaceContractLinkEntry]
    total_contracts: int
    total_links: int
    by_type: dict[str, int] = {}


class WorkspaceContractDetail(BaseModel):
    """One contract, keyed by ``(repo, file_path, contract_id)``.

    Carries the request/response shape that the list endpoint deliberately
    withholds: ``schema`` is present on roughly a third of contracts and single
    rows run to full inline type declarations, so it is affordable one at a time
    and not 200 at a time.
    """

    contract: WorkspaceContractEntry
    #: The artifact's ``schema`` block, named around Pydantic — a field literally
    #: called ``schema`` shadows an attribute of ``BaseModel``.
    contract_schema: dict | None = None
    #: Links this contract participates in, on whichever side it plays.
    links: list[WorkspaceContractLinkEntry] = []
    #: Why this consumer matched no provider (``external_host``,
    #: ``internal_only``, ``no_provider``), from the system graph's diagnostics.
    #: None for providers, for linked consumers, and when no graph is built.
    unmatched_reason: str | None = None


class WorkspaceCoChangeEntry(BaseModel):
    source_repo: str
    source_file: str
    target_repo: str
    target_file: str
    strength: float
    frequency: int
    last_date: str


class WorkspaceCoChangesResponse(BaseModel):
    co_changes: list[WorkspaceCoChangeEntry]
    #: Pairs matching the query, before ``limit`` paged them.
    total: int
    #: Pairs the miner scored before its edge caps trimmed the stored overlay.
    #: Larger than ``total`` means the artifact itself is a sample. Not a count
    #: of every pair in git history — each session's file list is bounded
    #: before pairing.
    total_mined: int = 0


class WorkspaceGraphNode(BaseModel):
    repo_id: str
    name: str
    file_count: int = 0
    coverage_pct: float = 0.0
    health_score: float = 0.0
    health_score_source: str = "derived"
    top_language: str = "unknown"


class WorkspaceGraphEdge(BaseModel):
    source: str
    target: str
    type: str  # "contract" or "co_change"
    strength: float = 0.0
    label: str | None = None


class WorkspaceGraphResponse(BaseModel):
    nodes: list[WorkspaceGraphNode] = []
    edges: list[WorkspaceGraphEdge] = []


# ---------------------------------------------------------------------------
# System graph (service-granular) + extraction diagnostics
# Mirrors repowise.core.workspace.{system_graph,diagnostics}.
# ---------------------------------------------------------------------------


class WorkspaceSystemNode(BaseModel):
    id: str
    repo: str
    service_path: str | None = None
    name: str
    kind: str = "service"
    provider_count: int = 0
    consumer_count: int = 0
    contract_types: list[str] = []
    is_orphan_provider: bool = False
    is_orphan_consumer: bool = False
    is_isolated: bool = False


class WorkspaceSystemEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str  # http | grpc | event | package | co_change | db
    match_type: str  # exact | candidate | manual | inferred
    confidence: float = 0.0
    weight: int = 1
    structural: bool = True
    contract_refs: list[str] = []


class WorkspaceRepoDiagnostics(BaseModel):
    repo: str
    providers_by_type: dict[str, int] = {}
    consumers_by_type: dict[str, int] = {}
    provider_count: int = 0
    consumer_count: int = 0
    providers_by_layer: dict[str, int] = {}
    consumers_by_layer: dict[str, int] = {}
    http_consumers_unresolved: int = 0


class WorkspaceUnmatchedConsumer(BaseModel):
    repo: str
    file_path: str
    contract_id: str
    contract_type: str
    reason: str  # no_provider | internal_only | unlinked


class WorkspaceOrphanProvider(BaseModel):
    repo: str
    file_path: str
    contract_id: str
    contract_type: str


class WorkspaceExtractionDiagnostics(BaseModel):
    total_providers: int = 0
    total_consumers: int = 0
    total_links: int = 0
    weak_link_count: int = 0
    repo_breakdown: list[WorkspaceRepoDiagnostics] = []
    unmatched_consumers: list[WorkspaceUnmatchedConsumer] = []
    unmatched_by_reason: dict[str, int] = {}
    orphan_providers: list[WorkspaceOrphanProvider] = []
    providers_by_layer: dict[str, int] = {}
    consumers_by_layer: dict[str, int] = {}
    http_consumers_unresolved: int = 0
    #: Share of located HTTP client calls that became a contract. ``None`` when
    #: none were located — 0/0 is not 100%.
    http_consumer_coverage: float | None = None


class WorkspaceSystemGraphResponse(BaseModel):
    version: int = 1
    generated_at: str = ""
    nodes: list[WorkspaceSystemNode] = []
    edges: list[WorkspaceSystemEdge] = []
    diagnostics: WorkspaceExtractionDiagnostics = WorkspaceExtractionDiagnostics()


# ---------------------------------------------------------------------------
# Cross-repo blast radius (reachability over the system graph)
# Mirrors repowise.core.workspace.blast_radius.
# ---------------------------------------------------------------------------


class WorkspaceImpactedNode(BaseModel):
    id: str
    repo: str
    name: str
    kind: str = "service"
    distance: int
    score: float
    structural: bool
    edge_kinds: list[str] = []


class WorkspaceBlastRadiusResponse(BaseModel):
    targets: list[str] = []
    target_repos: list[str] = []
    impacted: list[WorkspaceImpactedNode] = []
    impacted_repos: list[str] = []
    structural_count: int = 0
    behavioral_count: int = 0
    max_distance: int = 0
    total_impacted: int = 0
    unresolved_targets: list[str] = []


# ---------------------------------------------------------------------------
# Breaking-change guard — provider changes that break consumers across repos.
# Mirrors repowise.core.workspace.breaking_change.
# ---------------------------------------------------------------------------


class WorkspaceImpactedConsumer(BaseModel):
    repo: str
    service: str | None = None
    node_id: str
    file: str
    symbol: str  # display label; symbol_id is the one a tool can look up
    symbol_id: str | None = None
    match_type: str = "exact"
    confidence: float = 0.0


class WorkspaceBreakingChange(BaseModel):
    kind: str
    severity: str  # breaking | warning
    contract_id: str
    contract_type: str
    provider_repo: str
    provider_file: str
    provider_symbol: str
    provider_service: str | None = None
    provider_node_id: str = ""
    detail: str
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    impacted_consumers: list[WorkspaceImpactedConsumer] = []


class WorkspaceBreakingChangesResponse(BaseModel):
    version: int = 1
    #: ``None`` when detection has not run. An empty ``changes`` list means
    #: "nothing broke" only when this is set.
    generated_at: str | None = None
    changes: list[WorkspaceBreakingChange] = []
    total: int = 0
    breaking_count: int = 0
    warning_count: int = 0
    impacted_repos: list[str] = []
    impacted_services: list[str] = []
    total_impacted_consumers: int = 0


# ---------------------------------------------------------------------------
# Architecture conformance — rule violations + dependency cycles.
# Mirrors repowise.core.workspace.{conformance,cycles}.
# ---------------------------------------------------------------------------


class WorkspaceConformanceViolation(BaseModel):
    rule_source: str
    rule_target: str
    rule_description: str = ""
    source: str
    source_name: str
    target: str
    target_name: str
    edge_id: str
    edge_kind: str
    severity: str = "violation"


class WorkspaceDependencyCycle(BaseModel):
    nodes: list[str] = []
    edge_ids: list[str] = []
    length: int = 0


class WorkspaceConformanceResponse(BaseModel):
    version: int = 1
    #: ``None`` when the check has not run. Zero violations means "clean" only
    #: when this is set.
    generated_at: str | None = None
    rules_evaluated: int = 0
    violations: list[WorkspaceConformanceViolation] = []
    cycles: list[WorkspaceDependencyCycle] = []
    violation_count: int = 0
    #: ``cycle_count`` is how many cycles are listed; ``total_cycles`` is how
    #: many exist. They differ when MAX_CYCLES truncated the list.
    cycle_count: int = 0
    total_cycles: int = 0
    violating_repos: list[str] = []


class WorkspaceNodeArchitectureRole(BaseModel):
    id: str
    repo: str = ""
    name: str = ""
    visibility_fan_in: int = 0
    visibility_fan_out: int = 0
    role: str = "peripheral"


class WorkspaceArchitectureResponse(BaseModel):
    """Architecture-complexity metrics over the system graph (Phase 6)."""

    node_count: int = 0
    structural_edge_count: int = 0
    propagation_cost: float = 0.0
    propagation_cost_pct: float = 0.0
    core_size: int = 0
    core_ratio: float = 0.0
    core_members: list[str] = []
    cycle_count: int = 0
    conformance_violations: int = 0
    architecture_type: str = "hierarchical"
    score: float = 10.0
    role_breakdown: dict[str, int] = {}
    roles: list[WorkspaceNodeArchitectureRole] = []
    generated_at: str = ""
