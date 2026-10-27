"""Workspace (multi-repo) request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkspaceRepoRemovedResponse(BaseModel):
    """Response returned when a repo is removed from the workspace config."""

    ok: bool = True
    alias: str
    remaining_repos: int


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
    # Pages whose freshness status is fresh, out of ``page_count``.
    fresh_page_count: int = 0
    # Mean wiki page confidence, 0-100. Not a share of files documented.
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
    package_diagnostic_count: int = 0
    package_diagnostics_emitted: int = 0
    package_diagnostic_codes: list[str] = []
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
    #: What established the contract (``route_registration``, ``client_call``,
    #: ``sql_string``...), one of the closed origins in
    #: ``repowise.core.workspace.origins``. None on a row stored before origins.
    origin: str | None = None
    #: Extractor-supplied detail (``extraction_layer``, ``framework``, ``method``,
    #: ``path``, ``table``, ``package``...). Keys vary by contract type.
    meta: dict = {}


class WorkspaceContractLinkEntry(BaseModel):
    contract_id: str
    contract_type: str
    match_type: str
    #: The matching rule that made the link (``exact_id``, ``binding_routed``,
    #: ``prefix_stripped``...), one of ``repowise.core.workspace.origins``.
    #: ``match_type`` is derived from it.
    strategy: str | None = None
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
    #: The consumer's own contract id when it is not spelled like ``contract_id``,
    #: which names the provider's (a queue bound to that exchange, or a path
    #: matched across case, a wildcard method or a mount prefix); None otherwise.
    consumer_contract_id: str | None = None


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
    #: ``internal_only``, ``no_provider``, ``ambiguous_provider``), from the
    #: system graph's diagnostics.
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


class WorkspacePackageDepEntry(BaseModel):
    """One manifest dependency of one repo on another, as ``cross_repo_edges.json`` stores it."""

    source_repo: str
    source_manifest: str = ""
    target_repo: str
    target_package: str = ""
    #: How the dependency resolved (``npm_package``, ``dotnet_project_ref``...).
    kind: str = ""
    target_manifest: str = ""
    requested_version: str | None = None
    scope: str = ""
    resolution_basis: str = ""


class WorkspaceServiceLinkEntry(BaseModel):
    """One piece of evidence behind an edge between two services.

    ``evidence`` says which of ``link``, ``package_dep`` or ``co_change`` is set.
    """

    #: The system-graph edge kind it folds into (``http``, ``event``, ``db``,
    #: ``package``, ``co_change``...).
    kind: str
    #: ``a_to_b`` when service ``a`` is the consumer or dependent, ``b_to_a``
    #: when ``b`` is, ``both`` for a co-change (it has no direction).
    direction: Literal["a_to_b", "b_to_a", "both"]
    evidence: Literal["contract", "package", "co_change"]
    #: A contract link or service link (host-only, gateway route).
    link: WorkspaceContractLinkEntry | None = None
    #: What established each end's contract (``route_registration``,
    #: ``client_call``...), one of ``repowise.core.workspace.origins``. None
    #: when that end has no stored contract row or predates origins.
    provider_origin: str | None = None
    consumer_origin: str | None = None
    package_dep: WorkspacePackageDepEntry | None = None
    co_change: WorkspaceCoChangeEntry | None = None


class WorkspaceServiceLinksTotal(BaseModel):
    kind: str
    direction: Literal["a_to_b", "b_to_a", "both"]
    count: int


class WorkspaceLinksBetweenResponse(BaseModel):
    """Links between two services, read from ``system_graph.json``, ``contracts.json``
    and ``cross_repo_edges.json``. The three are written together by one
    workspace update; ``stale_graph`` is true when the graph and the contracts
    record different repo heads, and rows may then be missing."""

    a: str
    b: str
    stale_graph: bool = False
    #: One page of rows, ordered by direction, kind, evidence, then ids and files.
    links: list[WorkspaceServiceLinkEntry]
    #: Rows matching every filter; the page is cut from these.
    total: int
    #: Rows per edge kind and direction, for every kind present, under ``q``
    #: but not ``kind``. Kinds are not grouped here: a view that shows kind
    #: families (runtime, data, build, history) sums them itself.
    totals: list[WorkspaceServiceLinksTotal]


class WorkspaceCoChangesResponse(BaseModel):
    co_changes: list[WorkspaceCoChangeEntry]
    #: Pairs matching the query, before ``limit`` paged them.
    total: int
    #: Pairs the miner scored before its edge caps trimmed the stored overlay.
    #: Larger than ``total`` means the artifact itself is a sample. Not a count
    #: of every pair in git history — each session's file list is bounded
    #: before pairing.
    total_mined: int = 0
    #: The most pairs the miner keeps for any one repository pair, so a page
    #: can say why every repository pair stops at the same count.
    per_repo_pair_cap: int | None = None
    #: The most pairs the miner keeps across the whole workspace.
    total_cap: int | None = None
    #: Which cap trimmed the stored overlay: ``"total"``, ``"per_repo_pair"``,
    #: or None when nothing was dropped.
    truncated_by: Literal["total", "per_repo_pair"] | None = None


class WorkspaceCoChangeStructure(BaseModel):
    """What declared structure connects one co-changing file pair."""

    #: Contract links where one of the two files provides and the other consumes.
    pair_links: list[WorkspaceContractLinkEntry]
    #: Contract links between the two repositories through any files.
    repo_links_total: int
    repo_links_by_type: dict[str, int]
    #: Contract links touching each file, whatever the other end.
    source_file_links: int
    target_file_links: int


class WorkspaceGraphNode(BaseModel):
    repo_id: str
    name: str
    file_count: int = 0
    #: Mean wiki page confidence, 0-100, like the entry's ``doc_coverage_pct``.
    coverage_pct: float = 0.0
    #: The repo's code health, 0-100; None when health has not scored it.
    health_score: float | None = None
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
    #: Id of the service group this node belongs to; None when ungrouped.
    group: str | None = None


class WorkspaceServiceGroup(BaseModel):
    id: str
    label: str
    source: str  # tag | name | linked
    member_count: int = 0


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
    #: Strategy of the link that gave the edge its confidence (``manifest`` on
    #: a package edge); None on a co-change edge.
    strategy: str | None = None
    #: runtime | data | build | history, from core's one kind-to-family table.
    #: Filled from ``kind`` for an artifact written before edges carried it.
    family: str | None = None

    @model_validator(mode="after")
    def _fill_family(self) -> WorkspaceSystemEdge:
        if self.family is None:
            from repowise.core.workspace.system_graph import edge_family

            self.family = edge_family(self.kind)
        return self


class WorkspaceRepoDiagnostics(BaseModel):
    repo: str
    providers_by_type: dict[str, int] = {}
    consumers_by_type: dict[str, int] = {}
    provider_count: int = 0
    consumer_count: int = 0
    providers_by_layer: dict[str, int] = {}
    consumers_by_layer: dict[str, int] = {}
    http_consumers_unresolved: int = 0
    topic_names_unresolved: int = 0


class WorkspaceUnmatchedConsumer(BaseModel):
    repo: str
    file_path: str
    contract_id: str
    contract_type: str
    reason: str  # no_provider | internal_only | unlinked | external_host | ambiguous_provider


class WorkspaceOrphanProvider(BaseModel):
    repo: str
    file_path: str
    contract_id: str
    contract_type: str
    #: Other files of the same repo and service declaring the same id.
    other_files: list[str] = []


class WorkspaceSymbolIdentity(BaseModel):
    total: int = 0
    bound: int = 0
    unindexed_file: int = 0
    bound_ratio: float | None = None
    bound_ratio_indexed: float | None = None


class WorkspaceSchemaCoverage(BaseModel):
    total: int = 0
    bound: int = 0
    recovered: int = 0
    shared_symbol: int = 0
    unsupported_language: int = 0
    non_callable: int = 0
    eligible: int = 0
    recovered_ratio: float | None = None
    recovered_ratio_eligible: float | None = None


class WorkspaceCodeApiCoverage(BaseModel):
    manifests: int = 0
    published: int = 0
    unsupported_ecosystem: int = 0
    providers: int = 0
    consumers: int = 0
    linked_providers: int = 0
    published_ratio: float | None = None
    linked_ratio: float | None = None


class WorkspaceOpenApiCoverage(BaseModel):
    documents: int = 0
    parsed_documents: int = 0
    unresolved_documents: int = 0
    operations: int = 0
    providers: int = 0
    schemas_merged: int = 0
    spec_only_providers: int = 0
    request_states: dict[str, int] = {}
    response_states: dict[str, int] = {}
    refusal_reasons: dict[str, int] = {}


class WorkspaceExtractionDiagnostics(BaseModel):
    total_providers: int = 0
    total_consumers: int = 0
    total_links: int = 0
    weak_link_count: int = 0
    repo_breakdown: list[WorkspaceRepoDiagnostics] = []
    unmatched_consumers: list[WorkspaceUnmatchedConsumer] = []
    unmatched_by_reason: dict[str, int] = {}
    orphan_providers: list[WorkspaceOrphanProvider] = []
    #: Consumers served by their own repo + service, kept out of the unmatched list.
    in_service_consumers: list[WorkspaceUnmatchedConsumer] = []
    providers_by_layer: dict[str, int] = {}
    consumers_by_layer: dict[str, int] = {}
    http_consumers_unresolved: int = 0
    #: Share of client calls that became an HTTP contract, counting one miss per
    #: file that calls out through code no dialect reads. ``None`` when nothing
    #: was seen (0/0 is not 100%) or the artifact predates that count.
    http_consumer_coverage: float | None = None
    #: HTTP consumers that linked to another service, and their share of the
    #: same denominator. ``None`` on an artifact written before the count.
    http_consumers_linked: int | None = None
    http_link_coverage: float | None = None
    #: Per repo, files with a network-call token and no consumer contract.
    #: ``None`` on an artifact written before the count existed.
    unrecognised_call_files: dict[str, int] | None = None
    unrecognised_call_file_count: int | None = None
    #: Broker calls located whose topic or queue name could not be settled.
    topic_names_unresolved: int = 0
    #: Per repo, files using a messaging SDK and no topic contract.
    #: ``None`` on an artifact written before the count existed.
    unrecognised_message_files: dict[str, int] | None = None
    unrecognised_message_file_count: int | None = None

    symbol_identity: dict[str, WorkspaceSymbolIdentity] = {}
    schema_coverage: WorkspaceSchemaCoverage = WorkspaceSchemaCoverage()
    code_api: WorkspaceCodeApiCoverage = WorkspaceCodeApiCoverage()
    openapi: WorkspaceOpenApiCoverage = WorkspaceOpenApiCoverage()
    model_config = ConfigDict(extra="forbid")


class WorkspaceEvidenceReason(BaseModel):
    """One reason a graph's evidence is short of measured."""

    code: str
    #: The state this reason alone implies: ``partial`` or ``insufficient``.
    state: str
    message: str
    subjects: list[str] = []


class WorkspaceGraphEvidence(BaseModel):
    """How much evidence the system graph rests on.

    Mirrors ``repowise.core.workspace.graph_evidence.GraphEvidence``. When
    ``determined`` is false, a score, cycle verdict or impact set over the
    graph would be vacuous and is reported as not determined.
    """

    state: Literal["measured", "partial", "insufficient"]
    determined: bool
    summary: str = ""
    reasons: list[WorkspaceEvidenceReason] = []
    services: int = 0
    runtime_edges: int = 0
    build_edges: int = 0
    providers: int = 0
    consumers: int = 0


class WorkspaceSystemGraphResponse(BaseModel):
    version: int = 1
    generated_at: str = ""
    nodes: list[WorkspaceSystemNode] = []
    edges: list[WorkspaceSystemEdge] = []
    diagnostics: WorkspaceExtractionDiagnostics = WorkspaceExtractionDiagnostics()
    #: Service groups; empty when the estate is too small to group.
    groups: list[WorkspaceServiceGroup] = []


class WorkspaceSystemMapEdge(WorkspaceSystemEdge):
    """An edge of the map's lean view. Mirrors ``core.workspace.system_map_view``."""

    #: A co-change edge's file pairs; None on a structural edge, whose evidence
    #: the contracts routes serve.
    contract_refs: list[str] | None = None
    #: Distinct refs the full edge keeps.
    ref_count: int = 0


class WorkspaceSystemGraphMapResponse(BaseModel):
    """``GET /api/workspace/system-graph?view=map``: the graph without diagnostics."""

    version: int = 1
    generated_at: str = ""
    nodes: list[WorkspaceSystemNode] = []
    edges: list[WorkspaceSystemMapEdge] = []
    #: Service groups; empty when the estate is too small to group.
    groups: list[WorkspaceServiceGroup] = []


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
    #: Resolved targets no structural edge touches: an empty reach for one of
    #: these means "not seen", not "nothing depends on it".
    undetermined_targets: list[str] = []
    evidence: WorkspaceGraphEvidence | None = None


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
    #: Looked-up id for the changed provider symbol; ``None`` when the
    #: contract did not resolve to one.
    provider_symbol_id: str | None = None
    provider_service: str | None = None
    provider_node_id: str = ""
    detail: str
    side: str | None = None
    comparison_source: str | None = None
    comparison_key: str | None = None
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
    #: Edge kinds on each leg (``leg_kinds[i]`` joins ``nodes[i]`` to the next).
    leg_kinds: list[list[str]] = []
    #: Legs only an asynchronous (event) edge joins.
    async_legs: int = 0


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
    #: True when the enumeration budget stopped the count, so ``total_cycles``
    #: is a lower bound ("at least N").
    total_capped: bool = False
    violating_repos: list[str] = []
    #: False when the graph had too little evidence for "no cycles" to mean
    #: anything; ``None`` on a report written before evidence was recorded.
    cycles_determined: bool | None = None
    evidence: WorkspaceGraphEvidence | None = None


class WorkspaceNodeArchitectureRole(BaseModel):
    id: str
    repo: str = ""
    name: str = ""
    visibility_fan_in: int = 0
    visibility_fan_out: int = 0
    role: str = "peripheral"


class WorkspaceArchitectureResponse(BaseModel):
    """Architecture-complexity metrics over the system graph (Phase 6).

    Every measured figure is ``None`` when there is no graph, or when its
    ``evidence`` is insufficient: nothing seen is not a 10/10.
    """

    node_count: int = 0
    structural_edge_count: int = 0
    propagation_cost: float | None = None
    propagation_cost_pct: float | None = None
    core_size: int | None = None
    core_ratio: float | None = None
    core_members: list[str] = []
    cycle_count: int | None = None
    #: True when the enumeration budget stopped the count ("at least N").
    cycle_count_capped: bool = False
    conformance_violations: int = 0
    architecture_type: str = "undetermined"
    score: float | None = None
    role_breakdown: dict[str, int] = {}
    roles: list[WorkspaceNodeArchitectureRole] = []
    generated_at: str = ""
    evidence: WorkspaceGraphEvidence | None = None


# ---------------------------------------------------------------------------
# Cross-repo test impact: which consumer tests guard a provider change.
# Mirrors repowise.core.workspace.test_impact.workspace_test_impact_to_dict.
# ---------------------------------------------------------------------------


class WorkspaceTestRecommendation(BaseModel):
    """A test in a consumer repo that guards a changed provider file."""

    test_id: str = ""
    test_file: str = ""
    consumer_repo: str = ""
    #: Every consumer file that reached this test. One test can guard several
    #: call sites, and merging rows on the test would otherwise drop all but
    #: one of the files that led to it.
    consumer_files: list[str] = []
    #: The symbols the contracts bound to in the consumer. Carried through the
    #: boundary so a caller can look the call sites up instead of guessing them
    #: from the files.
    consumer_symbol_ids: list[str] = []
    provider_repo: str = ""
    contract_ids: list[str] = []
    contract_types: list[str] = []
    basis: str = "inferred"  # measured | inferred
    via: str = ""  # coverage-map | call-graph | import-graph
    confidence: float = 0.0
    source_files: list[str] = []
    evidence: list[dict] = []


class WorkspaceUnresolvedLink(BaseModel):
    """A contract link the join could not follow, and why."""

    consumer_repo: str = ""
    consumer_file: str = ""
    consumer_symbol_id: str | None = None
    provider_repo: str = ""
    provider_file: str = ""
    contract_id: str = ""
    contract_type: str = ""
    reason: str = ""  # no_index | unbound | symbol_missing | lookup_failed
    detail: str | None = None


class WorkspaceTestImpactFile(BaseModel):
    """One consumer file the join looked at, and the state it ended in."""

    consumer_repo: str = ""
    consumer_file: str = ""
    state: str = "none"  # measured | inferred | none | unresolved
    measured_tests_count: int = 0
    inferred_tests_count: int = 0
    via: str | None = None
    provider_repos: list[str] = []
    contract_ids: list[str] = []
    consumer_symbol_ids: list[str] = []


class WorkspaceTestImpactResponse(BaseModel):
    """``GET /api/workspace/test-impact``: consumer tests for a provider change."""

    workspace: bool = True
    recommendations: list[WorkspaceTestRecommendation] = []
    recommendations_total: int = 0
    recommendations_emitted: int = 0
    recommendations_truncated: bool = False
    recommendations_omitted: int = 0
    recommendations_by_basis: dict[str, int] = {}
    recommendations_by_repo: dict[str, int] = {}
    recommendations_by_consumer_repo: dict[str, int] = {}
    unresolved: list[WorkspaceUnresolvedLink] = []
    files_analyzed: list[WorkspaceTestImpactFile] = []
    #: Free-form counters and the state that produced an empty answer, so an
    #: empty response always names its reason.
    summary: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Estate report: the ``repowise report estate`` document, as
# repowise.core.report.estate.build_estate_document returns it.
# ---------------------------------------------------------------------------


class WorkspaceEstateCheckDocs(BaseModel):
    url: str
    short: str


class WorkspaceEstateCheck(BaseModel):
    """One evidence item: OpenSSF Scorecard check keys plus ``method`` and ``basis``.

    ``score`` is 0-10, -1 when inconclusive, ``None`` on a descriptive item.
    ``data`` holds the item's full figures; its keys depend on ``name``.
    """

    name: str
    score: float | None = None
    reason: str
    details: list[str] = []
    method: str
    basis: Literal["measured", "inferred", "declared", "unresolved", "gated"]
    data: dict[str, Any] | None = None
    documentation: WorkspaceEstateCheckDocs | None = None


class WorkspaceEstateSection(BaseModel):
    id: str
    title: str
    summary: str
    #: ``None`` for a workspace-scope section, else the member's alias.
    repo: str | None = None
    checks: list[WorkspaceEstateCheck] = []
    #: The section raised; its one check says why and is inconclusive.
    failed: bool = False


class WorkspaceEstateSnapshot(BaseModel):
    taken_at: str
    stamp: str | None = None


class WorkspaceEstateRepo(BaseModel):
    alias: str | None = None
    name: str
    head_commit: str | None = None
    last_snapshot: WorkspaceEstateSnapshot | None = None
    #: ``False`` when the member has no index at all.
    indexed: bool = True
    #: Set when an index exists but was not read: ``behind`` (written by an
    #: older repowise; run ``repowise update``) or ``unreadable``.
    index_state: Literal["behind", "unreadable"] | None = None


class WorkspaceEstateGenerator(BaseModel):
    name: str
    version: str
    stamp: str


class WorkspaceEstateScope(BaseModel):
    kind: Literal["workspace"]
    name: str


class WorkspaceEstateResponse(BaseModel):
    """``GET /api/workspace/estate``: the estate report document."""

    schema_version: int
    kind: Literal["estate"]
    generated_at: str
    generator: WorkspaceEstateGenerator
    scope: WorkspaceEstateScope
    notice: str
    repos: list[WorkspaceEstateRepo] = []
    sections: list[WorkspaceEstateSection] = []
