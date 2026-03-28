"""Pydantic request/response models for the repowise REST API."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class RepoCreate(BaseModel):
    name: str
    local_path: str
    url: str = ""
    default_branch: str = "main"
    settings: dict | None = None


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
    top_authors: list[dict]
    significant_commits: list[dict]
    co_change_partners: list[dict]
    is_hotspot: bool
    is_stable: bool
    churn_percentile: float
    age_days: int

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
            top_authors=json.loads(obj.top_authors_json),  # type: ignore[attr-defined]
            significant_commits=json.loads(obj.significant_commits_json),  # type: ignore[attr-defined]
            co_change_partners=json.loads(obj.co_change_partners_json),  # type: ignore[attr-defined]
            is_hotspot=obj.is_hotspot,  # type: ignore[attr-defined]
            is_stable=obj.is_stable,  # type: ignore[attr-defined]
            churn_percentile=obj.churn_percentile,  # type: ignore[attr-defined]
            age_days=obj.age_days,  # type: ignore[attr-defined]
        )


class HotspotResponse(BaseModel):
    file_path: str
    commit_count_90d: int
    churn_percentile: float
    primary_owner: str | None
    is_hotspot: bool


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
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


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
