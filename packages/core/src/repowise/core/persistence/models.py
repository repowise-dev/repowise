"""SQLAlchemy ORM models for repowise persistence layer.

All models use SQLAlchemy 2.0 declarative style with Mapped[] type annotations.
JSON blobs are stored as Text columns; the CRUD layer handles serialization.
The embedding column for pgvector is added conditionally by the Alembic migration
and is not declared here (keeps models dialect-neutral).

Note: the ORM symbol model is named WikiSymbol (not Symbol) to avoid shadowing
repowise.core.ingestion.models.Symbol in files that import from both modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _new_uuid() -> str:
    return uuid4().hex


def _now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    head_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Page(Base):
    """A generated wiki page.

    The primary key is page_id: "{page_type}:{target_path}" — same format as
    GeneratedPage.page_id. This is a natural key so callers can upsert without
    knowing the database row ID.
    """

    __tablename__ = "wiki_pages"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    page_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    target_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="fresh")
    # JSON-encoded dict (metadata is a reserved SQLAlchemy attribute name)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PageVersion(Base):
    """Historical snapshot of a wiki page, created each time the page is re-generated."""

    __tablename__ = "wiki_page_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    page_id: Mapped[str] = mapped_column(Text, ForeignKey("wiki_pages.id"), nullable=False)
    repository_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    page_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    # Relative file path (the logical node identifier within a repo)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, default="file")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_entry_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pagerank: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    betweenness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    community_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "node_id", name="uq_graph_node"),)


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    source_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    imported_names_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edge_type: Mapped[str | None] = mapped_column(String(64), nullable=True, default="imports")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (
        UniqueConstraint("repository_id", "source_node_id", "target_node_id", name="uq_graph_edge"),
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class WikiSymbol(Base):
    """ORM representation of a code symbol.

    Named WikiSymbol (not Symbol) to avoid shadowing
    repowise.core.ingestion.models.Symbol in files that import both.
    """

    __tablename__ = "wiki_symbols"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    # "{path}::{name}" — the ingestion Symbol.id field
    symbol_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    is_async: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    complexity_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    parent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "symbol_id", name="uq_wiki_symbol"),)


class GitMetadata(Base):
    """Per-file git history metadata: commit counts, ownership, co-change partners."""

    __tablename__ = "git_metadata"
    __table_args__ = (UniqueConstraint("repository_id", "file_path", name="uq_git_metadata"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    # Commit volume
    commit_count_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count_30d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timeline
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ownership
    primary_owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_owner_commit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # JSON fields (stored as Text, parsed/serialized in CRUD layer)
    top_authors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    significant_commits_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    co_change_partners_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Derived signals
    is_hotspot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_stable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    churn_percentile: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Diff size (Phase 2)
    lines_added_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_deleted_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_commit_size: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Commit classification (Phase 2)
    commit_categories_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Recent ownership & bus factor (Phase 2)
    recent_owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recent_owner_commit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    bus_factor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contributor_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Rename tracking & merge conflict proxy (Phase 3)
    original_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    merge_commit_count_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Temporal hotspot score: exponentially time-decayed churn signal
    temporal_hotspot_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class DecisionRecord(Base):
    """An architectural decision record captured from inline markers, git
    archaeology, README mining, or manual CLI entry."""

    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "title",
            "source",
            "evidence_file",
            name="uq_decision_record",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )

    # Core content
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="proposed"
    )  # proposed | active | deprecated | superseded
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # JSON arrays stored as Text (same pattern as GitMetadata.*_json)
    alternatives_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    consequences_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    affected_files_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    affected_modules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_commits_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Provenance
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="cli"
    )  # git_archaeology | inline_marker | readme_mining | cli
    evidence_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Staleness
    last_code_change: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    staleness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    superseded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class Conversation(Base):
    """A chat conversation for a repository."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class ChatMessage(Base):
    """A single message in a chat conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user | assistant
    content_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class LlmCost(Base):
    """A single LLM API call cost record."""

    __tablename__ = "llm_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class SecurityFinding(Base):
    """A security signal detected during file ingestion."""

    __tablename__ = "security_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class DeadCodeFinding(Base):
    """Dead code finding: unreachable files, unused exports, zombie packages."""

    __tablename__ = "dead_code_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # unreachable_file, unused_export, etc.
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbol_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    commit_count_90d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    safe_to_delete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    primary_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open"
    )  # open, acknowledged, resolved, false_positive
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
