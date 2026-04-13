"""Pydantic request/response models for the repowise REST API."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class RepoCreate(BaseModel):
    name: str
    local_path: str
    url: str = ""
    default_branch: str = "main"
    settings: dict | None = None

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, v: str) -> str:
        resolved = Path(v).resolve()
        if ".." in Path(v).parts:
            raise ValueError("local_path must not contain '..' segments")
        if not resolved.is_dir():
            raise ValueError(f"local_path does not exist or is not a directory: {resolved}")
        if not (resolved / ".git").exists():
            raise ValueError(f"local_path is not a git repository (no .git found): {resolved}")
        return str(resolved)


class RepoUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    default_branch: str | None = None
    settings: dict | None = None


class RepoResponse(BaseModel):
    id: str
    name: str
    url: str
    local_path: str
    default_branch: str
    head_commit: str | None
    settings: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> RepoResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            url=obj.url,  # type: ignore[attr-defined]
            local_path=obj.local_path,  # type: ignore[attr-defined]
            default_branch=obj.default_branch,  # type: ignore[attr-defined]
            head_commit=obj.head_commit,  # type: ignore[attr-defined]
            settings=json.loads(obj.settings_json),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class PageResponse(BaseModel):
    id: str
    repository_id: str
    page_type: str
    title: str
    content: str
    target_path: str
    source_hash: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    generation_level: int
    version: int
    confidence: float
    freshness_status: str
    metadata: dict
    human_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> PageResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            page_type=obj.page_type,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            content=obj.content,  # type: ignore[attr-defined]
            target_path=obj.target_path,  # type: ignore[attr-defined]
            source_hash=obj.source_hash,  # type: ignore[attr-defined]
            model_name=obj.model_name,  # type: ignore[attr-defined]
            provider_name=obj.provider_name,  # type: ignore[attr-defined]
            input_tokens=obj.input_tokens,  # type: ignore[attr-defined]
            output_tokens=obj.output_tokens,  # type: ignore[attr-defined]
            cached_tokens=obj.cached_tokens,  # type: ignore[attr-defined]
            generation_level=obj.generation_level,  # type: ignore[attr-defined]
            version=obj.version,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            freshness_status=obj.freshness_status,  # type: ignore[attr-defined]
            metadata=json.loads(obj.metadata_json),  # type: ignore[attr-defined]
            human_notes=obj.human_notes,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class PageVersionResponse(BaseModel):
    id: str
    page_id: str
    version: int
    page_type: str
    title: str
    content: str
    source_hash: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    confidence: float
    archived_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> PageVersionResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            page_id=obj.page_id,  # type: ignore[attr-defined]
            version=obj.version,  # type: ignore[attr-defined]
            page_type=obj.page_type,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            content=obj.content,  # type: ignore[attr-defined]
            source_hash=obj.source_hash,  # type: ignore[attr-defined]
            model_name=obj.model_name,  # type: ignore[attr-defined]
            provider_name=obj.provider_name,  # type: ignore[attr-defined]
            input_tokens=obj.input_tokens,  # type: ignore[attr-defined]
            output_tokens=obj.output_tokens,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            archived_at=obj.archived_at,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobResponse(BaseModel):
    id: str
    repository_id: str
    status: str
    provider_name: str
    model_name: str
    total_pages: int
    completed_pages: int
    failed_pages: int
    current_level: int
    error_message: str | None
    config: dict
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_orm(cls, obj: object) -> JobResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            provider_name=obj.provider_name,  # type: ignore[attr-defined]
            model_name=obj.model_name,  # type: ignore[attr-defined]
            total_pages=obj.total_pages,  # type: ignore[attr-defined]
            completed_pages=obj.completed_pages,  # type: ignore[attr-defined]
            failed_pages=obj.failed_pages,  # type: ignore[attr-defined]
            current_level=obj.current_level,  # type: ignore[attr-defined]
            error_message=obj.error_message,  # type: ignore[attr-defined]
            config=json.loads(obj.config_json),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
            started_at=obj.started_at,  # type: ignore[attr-defined]
            finished_at=obj.finished_at,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    search_type: str = "semantic"
    limit: int = Field(default=10, ge=1, le=100)


class SearchResultResponse(BaseModel):
    page_id: str
    title: str
    page_type: str
    target_path: str
    score: float
    snippet: str
    search_type: str


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


class SymbolResponse(BaseModel):
    id: str
    repository_id: str
    file_path: str
    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    signature: str
    start_line: int
    end_line: int
    docstring: str | None
    visibility: str
    is_async: bool
    complexity_estimate: int
    language: str
    parent_name: str | None

    @classmethod
    def from_orm(cls, obj: object) -> SymbolResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            file_path=obj.file_path,  # type: ignore[attr-defined]
            symbol_id=obj.symbol_id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            qualified_name=obj.qualified_name,  # type: ignore[attr-defined]
            kind=obj.kind,  # type: ignore[attr-defined]
            signature=obj.signature,  # type: ignore[attr-defined]
            start_line=obj.start_line,  # type: ignore[attr-defined]
            end_line=obj.end_line,  # type: ignore[attr-defined]
            docstring=obj.docstring,  # type: ignore[attr-defined]
            visibility=obj.visibility,  # type: ignore[attr-defined]
            is_async=obj.is_async,  # type: ignore[attr-defined]
            complexity_estimate=obj.complexity_estimate,  # type: ignore[attr-defined]
            language=obj.language,  # type: ignore[attr-defined]
            parent_name=obj.parent_name,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class GraphNodeResponse(BaseModel):
    node_id: str
    node_type: str
    language: str
    symbol_count: int
    pagerank: float
    betweenness: float
    community_id: int
    is_test: bool = False
    is_entry_point: bool = False
    has_doc: bool = False


class GraphEdgeResponse(BaseModel):
    source: str
    target: str
    imported_names: list[str]


class GraphExportResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    links: list[GraphEdgeResponse]


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhookResponse(BaseModel):
    event_id: str
    status: str = "accepted"


# ---------------------------------------------------------------------------
# Git Intelligence
# ---------------------------------------------------------------------------


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
    test_gap: bool | None = None

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
            churn_percentile=obj.churn_percentile,  # type: ignore[attr-defined]
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
        )


class HotspotResponse(BaseModel):
    file_path: str
    commit_count_90d: int
    commit_count_30d: int
    churn_percentile: float
    temporal_hotspot_score: float | None = None
    primary_owner: str | None
    is_hotspot: bool
    is_stable: bool
    bus_factor: int
    contributor_count: int
    lines_added_90d: int
    lines_deleted_90d: int
    avg_commit_size: float
    commit_categories: dict


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


# ---------------------------------------------------------------------------
# Dead Code
# ---------------------------------------------------------------------------


class DeadCodeFindingResponse(BaseModel):
    id: str
    kind: str
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    lines: int
    safe_to_delete: bool
    primary_owner: str | None
    status: str
    note: str | None

    @classmethod
    def from_orm(cls, obj: object) -> DeadCodeFindingResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            kind=obj.kind,  # type: ignore[attr-defined]
            file_path=obj.file_path,  # type: ignore[attr-defined]
            symbol_name=obj.symbol_name,  # type: ignore[attr-defined]
            symbol_kind=obj.symbol_kind,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            reason=obj.reason,  # type: ignore[attr-defined]
            lines=obj.lines,  # type: ignore[attr-defined]
            safe_to_delete=obj.safe_to_delete,  # type: ignore[attr-defined]
            primary_owner=obj.primary_owner,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            note=obj.note,  # type: ignore[attr-defined]
        )


class DeadCodePatchRequest(BaseModel):
    status: str
    note: str | None = None


class DeadCodeSummaryResponse(BaseModel):
    total_findings: int
    confidence_summary: dict
    deletable_lines: int
    total_lines: int
    by_kind: dict


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class SecurityFindingResponse(BaseModel):
    id: int
    file_path: str
    kind: str
    severity: str
    snippet: str | None
    detected_at: datetime


# ---------------------------------------------------------------------------
# Repo Stats
# ---------------------------------------------------------------------------


class RepoStatsResponse(BaseModel):
    file_count: int
    symbol_count: int
    entry_point_count: int
    doc_coverage_pct: float
    freshness_score: float
    dead_export_count: int


# ---------------------------------------------------------------------------
# Module Graph
# ---------------------------------------------------------------------------


class ModuleNodeResponse(BaseModel):
    module_id: str
    file_count: int
    symbol_count: int
    avg_pagerank: float
    doc_coverage_pct: float


class ModuleEdgeResponse(BaseModel):
    source: str
    target: str
    edge_count: int


class ModuleGraphResponse(BaseModel):
    nodes: list[ModuleNodeResponse]
    edges: list[ModuleEdgeResponse]


# ---------------------------------------------------------------------------
# Ego Graph
# ---------------------------------------------------------------------------


class EgoGraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    links: list[GraphEdgeResponse]
    center_node_id: str
    center_git_meta: GitMetadataResponse | None
    inbound_count: int
    outbound_count: int


class NodeSearchResult(BaseModel):
    node_id: str
    language: str
    symbol_count: int


# ---------------------------------------------------------------------------
# Dead Code Graph
# ---------------------------------------------------------------------------


class DeadCodeGraphNodeResponse(BaseModel):
    node_id: str
    node_type: str
    language: str
    symbol_count: int
    pagerank: float
    betweenness: float
    community_id: int
    is_test: bool = False
    is_entry_point: bool = False
    has_doc: bool = False
    confidence_group: str  # "certain" | "likely"


class DeadCodeGraphResponse(BaseModel):
    nodes: list[DeadCodeGraphNodeResponse]
    links: list[GraphEdgeResponse]


# ---------------------------------------------------------------------------
# Hot Files Graph
# ---------------------------------------------------------------------------


class HotFilesNodeResponse(BaseModel):
    node_id: str
    node_type: str
    language: str
    symbol_count: int
    pagerank: float
    betweenness: float
    community_id: int
    is_test: bool = False
    is_entry_point: bool = False
    has_doc: bool = False
    commit_count: int


class HotFilesGraphResponse(BaseModel):
    nodes: list[HotFilesNodeResponse]
    links: list[GraphEdgeResponse]


# ---------------------------------------------------------------------------
# Graph Intelligence
# ---------------------------------------------------------------------------


class SymbolNodeSummary(BaseModel):
    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int | None = None
    signature: str | None = None


class CallerCalleeEntry(BaseModel):
    symbol_id: str
    name: str
    kind: str
    file: str
    start_line: int | None = None
    edge_type: str
    confidence: float


class CallersCalleesResponse(BaseModel):
    symbol_id: str
    symbol: SymbolNodeSummary
    callers: list[CallerCalleeEntry]
    callees: list[CallerCalleeEntry]
    caller_count: int
    callee_count: int
    truncated: bool


class CommunityMember(BaseModel):
    path: str
    pagerank: float
    is_entry_point: bool


class NeighboringCommunity(BaseModel):
    community_id: int
    label: str
    cross_edge_count: int


class CommunityDetailResponse(BaseModel):
    community_id: int
    label: str
    cohesion: float
    member_count: int
    members: list[CommunityMember]
    truncated: bool
    neighboring_communities: list[NeighboringCommunity]


class CommunitySummaryItem(BaseModel):
    community_id: int
    label: str
    cohesion: float
    member_count: int
    top_file: str


class GraphMetricsResponse(BaseModel):
    target: str
    node_type: str
    pagerank: float
    pagerank_percentile: int
    betweenness: float
    betweenness_percentile: int
    community_id: int
    community_label: str | None
    is_entry_point: bool
    in_degree: int
    out_degree: int
    entry_point_score: float | None = None
    kind: str | None = None
    file: str | None = None


class ExecutionFlowEntry(BaseModel):
    entry_point: str
    entry_point_name: str
    entry_point_score: float
    trace: list[str]
    depth: int
    crosses_community: bool
    communities_visited: list[int]


class ExecutionFlowsResponse(BaseModel):
    total_entry_points: int
    flows: list[ExecutionFlowEntry]


# ---------------------------------------------------------------------------
# Blast Radius
# ---------------------------------------------------------------------------


class BlastRadiusRequest(BaseModel):
    changed_files: list[str]
    max_depth: int = Field(default=3, ge=1, le=10)


class DirectRiskEntry(BaseModel):
    path: str
    risk_score: float
    temporal_hotspot: float
    centrality: float


class TransitiveEntry(BaseModel):
    path: str
    depth: int


class CochangeWarning(BaseModel):
    changed: str
    missing_partner: str
    score: float


class ReviewerEntry(BaseModel):
    email: str
    files: int
    ownership_pct: float


class BlastRadiusResponse(BaseModel):
    direct_risks: list[DirectRiskEntry]
    transitive_affected: list[TransitiveEntry]
    cochange_warnings: list[CochangeWarning]
    recommended_reviewers: list[ReviewerEntry]
    test_gaps: list[str]
    overall_risk_score: float


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


class CoordinatorHealthResponse(BaseModel):
    sql_pages: int | None
    vector_count: int | None
    graph_nodes: int | None
    drift_pct: float | None
    status: str  # "ok" | "warning" | "critical"


# ---------------------------------------------------------------------------
# Knowledge Map
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class DecisionRecordResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    status: str
    context: str
    decision: str
    rationale: str
    alternatives: list[str]
    consequences: list[str]
    affected_files: list[str]
    affected_modules: list[str]
    tags: list[str]
    source: str
    evidence_commits: list[str]
    evidence_file: str | None
    evidence_line: int | None
    confidence: float
    staleness_score: float
    superseded_by: str | None
    last_code_change: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> DecisionRecordResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            context=obj.context,  # type: ignore[attr-defined]
            decision=obj.decision,  # type: ignore[attr-defined]
            rationale=obj.rationale,  # type: ignore[attr-defined]
            alternatives=json.loads(obj.alternatives_json),  # type: ignore[attr-defined]
            consequences=json.loads(obj.consequences_json),  # type: ignore[attr-defined]
            affected_files=json.loads(obj.affected_files_json),  # type: ignore[attr-defined]
            affected_modules=json.loads(obj.affected_modules_json),  # type: ignore[attr-defined]
            tags=json.loads(obj.tags_json),  # type: ignore[attr-defined]
            source=obj.source,  # type: ignore[attr-defined]
            evidence_commits=json.loads(obj.evidence_commits_json),  # type: ignore[attr-defined]
            evidence_file=obj.evidence_file,  # type: ignore[attr-defined]
            evidence_line=obj.evidence_line,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            staleness_score=obj.staleness_score,  # type: ignore[attr-defined]
            superseded_by=obj.superseded_by,  # type: ignore[attr-defined]
            last_code_change=obj.last_code_change,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class DecisionCreate(BaseModel):
    title: str
    context: str = ""
    decision: str = ""
    rationale: str = ""
    alternatives: list[str] = []
    consequences: list[str] = []
    affected_files: list[str] = []
    affected_modules: list[str] = []
    tags: list[str] = []


class DecisionStatusUpdate(BaseModel):
    status: str
    superseded_by: str | None = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None


class ConversationResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object, message_count: int = 0) -> ConversationResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            message_count=message_count,
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class ChatMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: dict
    created_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> ChatMessageResponse:
        content_str = obj.content_json  # type: ignore[attr-defined]
        try:
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
        except Exception:
            content = {"text": content_str}
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            conversation_id=obj.conversation_id,  # type: ignore[attr-defined]
            role=obj.role,  # type: ignore[attr-defined]
            content=content,
            created_at=obj.created_at,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class SetActiveProviderRequest(BaseModel):
    provider: str
    model: str | None = None


class SetApiKeyRequest(BaseModel):
    api_key: str


# ---------------------------------------------------------------------------
# Cost Tracking
# ---------------------------------------------------------------------------


class CostGroupResponse(BaseModel):
    group: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostSummaryResponse(BaseModel):
    total_cost_usd: float
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    since: str | None


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


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
