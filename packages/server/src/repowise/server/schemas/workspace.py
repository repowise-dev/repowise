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


class WorkspaceContractsResponse(BaseModel):
    contracts: list[WorkspaceContractEntry]
    links: list[WorkspaceContractLinkEntry]
    total_contracts: int
    total_links: int
    by_type: dict[str, int] = {}


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
    total: int


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


class WorkspaceSystemGraphResponse(BaseModel):
    version: int = 1
    generated_at: str = ""
    nodes: list[WorkspaceSystemNode] = []
    edges: list[WorkspaceSystemEdge] = []
    diagnostics: WorkspaceExtractionDiagnostics = WorkspaceExtractionDiagnostics()
