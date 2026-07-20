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
    Index,
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
    # Whole-history git totals captured at index time via cheap ``git rev-list``
    # calls. The per-commit ``git_commits`` table is deliberately bounded to the
    # newest N commits (churn/co-change need no more), so project age and total
    # commit count must be read from these repo-level fields rather than derived
    # from that sample — otherwise a multi-year repo reads as a few months old
    # (issue #730). NULL until the first index writes them / for non-git repos.
    total_commit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # All-time unique authors (mailmap-folded) and the founding author's name.
    # Contributor count shares the #730 bug when read off the bounded sample;
    # the founder rides along free (the root commit is already loaded for the
    # first-commit date). Both NULL until the first index writes them.
    total_contributor_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_commit_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    # 1-3 sentence purpose blurb. Always populated (LLM-extracted from content
    # for full mode, deterministic structure summary for index-only mode).
    # Surfaced by get_context as the default narrative; content is gated
    # behind include=["full_doc"] to keep MCP responses small.
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    # Developer-authored notes that survive LLM re-generation.
    human_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Relative file path (for file nodes) or symbol ID (for symbol nodes)
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
    community_meta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Symbol-level fields (null for file nodes)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qualified_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_symbol_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when this node represents an `external:*` import that we resolved to
    # a known third-party dependency declared in a manifest. Powers C4 L1.
    external_system_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("external_systems.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "node_id", name="uq_graph_node"),)


class ExternalSystem(Base):
    """A third-party dependency declared in a repo manifest (package.json,
    pyproject.toml, Cargo.toml, go.mod, .csproj).

    Populated during ingestion by repowise.core.ingestion.external_systems.
    Consumed by the C4 builder service to render L1 (System Context) and the
    external boundary of L2/L3.
    """

    __tablename__ = "external_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ecosystem: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="library")
    # Boundary type in {db, network, filesystem, subprocess, lock}; nullable.
    # NULL means "untyped" and every consumer (C4, perf, security) degrades
    # gracefully. Populated by ingestion.external_systems.io_kind.
    io_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    declared_in: Mapped[str] = mapped_column(Text, nullable=False)
    is_dev_dep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (
        UniqueConstraint("repository_id", "name", "declared_in", name="uq_external_system"),
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    source_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    imported_names_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edge_type: Mapped[str] = mapped_column(String(64), nullable=False, default="imports")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_graph_edge_typed",
        ),
    )


class GraphMetric(Base):
    """Materialized file-level graph metrics snapshot (large-repo scale).

    Lets metric reads be served from SQL without recomputing the expensive
    NetworkX centrality kernels on big graphs. Written after the graph is
    built (additive to ``graph_nodes``) and read back into a GraphBuilder via
    ``load_metrics_from_sql``.
    """

    __tablename__ = "graph_metrics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    pagerank: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    betweenness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    community_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_degree: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    out_degree: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "node_id", name="uq_graph_metric"),)


class GraphNodeMembership(Base):
    """Materialized component memberships — SCCs and symbol communities.

    Persists two structural facts the graph carries but never exposed as
    queryable rows: file-level strongly-connected components (import cycles,
    ``scc_id`` / ``scc_size`` with ``scc_size >= 2``) and symbol-level
    communities (``symbol_community_id``). The break-cycle and move-method
    refactoring detectors compute the same structure from the in-memory graph
    at health time; this snapshot lets the web layer read cycles and
    communities without rebuilding the graph. Additive to ``graph_nodes`` /
    ``graph_metrics``; non-load-bearing.
    """

    __tablename__ = "graph_node_membership"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    scc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scc_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbol_community_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (
        UniqueConstraint("repository_id", "node_id", name="uq_graph_node_membership"),
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

    # Prior-defect history: bug-fix commits touching this file in the trailing
    # ~6-month defect window (anchored to the index's as_of reference). Consumed
    # by the ``prior_defect`` health biomarker — a leakage-aware process signal.
    #
    # ``prior_defect_count`` keeps only fixes whose diff changes production code
    # (see ingestion.git_indexer.fix_shape); ``prior_defect_raw_count`` is every
    # subject-matched fix, kept alongside so the filtered-out noise stays
    # inspectable instead of silently vanishing from the count.
    prior_defect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prior_defect_raw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Bug-magnet rollup over this file's ``fix_events``, recomputed after every
    # index and update. ``fix_mass`` is ``prior_defect_count`` with a 90-day
    # half-life applied (analysis.health.fix_attribution), so a file whose fixes
    # all sit at the window's trailing edge stops looking like one fixed three
    # times this month; ``bug_magnet`` is that mass past the three-fresh-fixes
    # trigger. ``fix_symbol_counts_json`` maps ``WikiSymbol.symbol_id`` to how
    # many of those fixes landed in it. The mass is stored beside the flag on
    # purpose: a bare boolean cannot be argued with.
    fix_mass: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bug_magnet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_fix_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fix_symbol_counts_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Temporal hotspot score: exponentially time-decayed churn signal
    temporal_hotspot_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)

    # Change entropy (Hassan History Complexity Metric): decay-weighted sum of
    # per-commit scatter (log2(files-touched)/files-touched) and its repo-wide
    # percentile. Populated by the FULL-tier co-change walk.
    change_entropy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    change_entropy_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Agent-provenance rollup: how much of this file's indexed history is
    # agent-attributed (deterministic local-channel classification — identity
    # fields, message footers, co-author trailers; see
    # ingestion.git_indexer.agent_provenance). agent_tier_counts_json maps
    # autonomy tier ("1" near-autonomous / "2" human-driven / "3" assisted)
    # to commit counts. agent_authored_pct stays NULL until the next reindex.
    agent_commit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_authored_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    agent_tier_counts_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Line-level agent share (agent-trace standard): distinct file
    # lines an AI/mixed contributor wrote, interval-union deduped across every
    # trace record for this path, plus a {model_id: line_count} breakdown
    # (opus vs sonnet). The denominator ("N% AI-written") is the file's current
    # LOC, applied downstream — the git indexer stores only the AI numerator so
    # it stays LOC-decoupled. Both stay 0/"{}" unless the repo ships
    # .agent-trace/traces.jsonl. Model buckets can overlap (a line touched by
    # two models counts in both), so their sum may exceed agent_line_count.
    agent_line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_line_model_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class GitCommit(Base):
    """Per-commit git history: one row per commit in the indexed window.

    Captures the change-level signals the per-file ``GitMetadata`` aggregates
    away — diff size/diffusion (Kamei change metrics) and a calibrated
    just-in-time ``change_risk`` score — written during the same single
    repo-wide ``git log`` walk that builds the commit index (no extra git
    pass). The walk excludes merges, so every row is a real content change.
    Bounded by the indexer's ``commit_limit`` (newest-first), like the rest of
    the git data.
    """

    __tablename__ = "git_commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="uq_git_commit"),
        Index("ix_git_commits_repo_risk", "repository_id", "change_risk_score"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    sha: Mapped[str] = mapped_column(String(40), nullable=False)

    # Authorship + timeline
    author_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    author_email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Kamei change features (diff size + diffusion of THIS change)
    lines_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dirs_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subsystems_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entropy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_fix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Author experience at the time of the commit: the author's cumulative prior
    # commit count, reconstructed in-memory over the walk (no extra git pass).
    # The one change-risk feature not derivable from the diff alone — persisted
    # so the per-commit risk breakdown reproduces the stored score exactly.
    author_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Just-in-time change-risk: 0-10 score + level ("low"/"moderate"/"high")
    # from the calibrated linear ``change_risk`` model. Author experience is
    # computed in-memory across the walk (cumulative prior-commit count); the
    # score is pure arithmetic on already-parsed diff data (zero LLM, no blame).
    change_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Agent provenance: which coding agent (if any) authored this commit, at
    # what autonomy tier (1 near-autonomous bot account · 2 human-driven agent
    # · 3 assisted/co-authored), via which attribution channel, and with what
    # confidence band. NULL throughout = human-authored (or pre-migration rows;
    # back-populated on the next index). Deterministic local-git channels only.
    agent_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_autonomy_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Model that wrote the change, in models.dev ``provider/model`` form (e.g.
    # ``anthropic/claude-opus-4``), read from the agent-trace record that
    # attributed the commit. Set only for the ``agent_trace`` channel (no other
    # local channel carries a model); NULL for human or non-trace commits.
    agent_model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class FixEvent(Base):
    """One bug-fix commit's effect on one file, with its bug-introducing candidates.

    ``GitMetadata.prior_defect_count`` is the aggregate of these rows; this table
    is the evidence underneath it. Each row records what a fix commit did to one
    file (:mod:`ingestion.git_indexer.fix_shape` kind, the old-side line ranges it
    replaced, how many lines it changed) and, for ``code_fix`` rows, the ranked
    commits that ``git blame`` puts on those lines at ``fix^`` — the SZZ
    bug-introducing candidates.

    ``committed_at`` is load-bearing. Rows are stored **undecayed**: every recency
    weight downstream (biomarker mass, bug-magnet flag, rollups) is derived at read
    time from this column, so changing a half-life is a read-time decision and
    never needs a reindex.

    Joins: ``fix_sha`` and each inducing sha to :class:`GitCommit` (which already
    carries agent provenance), and ``file_path`` + ``old_ranges_json`` to
    :class:`WikiSymbol` / :class:`GitFunctionBlame` line ranges.

    Retention mirrors the ``prior_defect`` window: a full index seeds the trailing
    window, updates append newer fix commits and prune rows that have aged out, so
    the table always holds exactly the window a fresh index would produce.
    """

    __tablename__ = "fix_events"
    __table_args__ = (
        UniqueConstraint("repository_id", "fix_sha", "file_path", name="uq_fix_event"),
        Index("ix_fix_events_repo_path", "repository_id", "file_path"),
        Index("ix_fix_events_repo_time", "repository_id", "committed_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    fix_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    # fix_shape kind for the WHOLE commit, repeated on each of its file rows:
    # the classification is a property of the diff, not of one file in it.
    shape_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="code_fix")

    # Inclusive ``[[start, end], ...]`` spans on the PRE-fix file. Empty for a
    # pure insertion, which replaced nothing.
    old_ranges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    changed_loc: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # DEAD, always ``"[]"``. Once held ranked SZZ blame candidates naming the
    # commit that introduced each bug. Nothing ever read it: the surfaces that
    # would have are cut, so the blame pass that filled it was removed too. The
    # column stays because emptying it is free and dropping it is a table
    # rebuild. Do NOT read this as "traced and found nothing".
    inducing_shas_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Per-bucket changed-line counts from the fix taxonomy. Always empty: the
    # taxonomy classifier was measured and cut, never built.
    taxonomy_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # ``WikiSymbol.symbol_id``s whose CURRENT line span overlaps this row's
    # ``old_ranges_json``, so a file-level fix history can be read per symbol.
    # ``attribution`` says how much to trust the join: ``exact`` only when
    # nothing has touched the file since the fix (so the current spans are the
    # spans the fix saw), ``approximate`` when lines may have shifted underneath,
    # ``none`` when there was nothing to attribute — a pure insertion, an
    # unparsed file. See analysis.health.fix_attribution.
    symbol_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    attribution: Mapped[str] = mapped_column(String(16), nullable=False, default="none")

    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class GitFunctionBlame(Base):
    """Per-function blame rollup: function-granular git signals derived from the
    per-line ``BlameIndex`` during FULL-tier health analysis.

    The blame index is built once per file (one ``git blame`` call) and was
    previously consumed in-memory by the ``function_hotspot`` /
    ``code_age_volatility`` biomarkers and then discarded. This table persists
    the cheap per-function rollup (bounded by the number of *modified*
    functions) so a function-level health surface can read it without
    re-blaming: modification count, median line age, recent-modification count,
    and the blame owner over the function's line range. Raw per-line blame is
    NOT persisted (size ~ LOC x history; recomputable).

    Keyed ``(repository_id, symbol_id)`` where ``symbol_id = "{path}::{name}"``
    mirrors :class:`WikiSymbol.symbol_id`, so callers can join straight to the
    symbol graph.
    """

    __tablename__ = "git_function_blame"
    __table_args__ = (
        UniqueConstraint("repository_id", "symbol_id", name="uq_git_function_blame"),
        Index("ix_git_function_blame_repo_mods", "repository_id", "mod_count"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    # "{path}::{name}" — mirrors WikiSymbol.symbol_id.
    symbol_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    function_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Distinct commits touching the function's line range (its churn).
    mod_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Distinct commits touching the range within the recent window.
    recent_mod_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Median author time (unix seconds) over the range — a line-age proxy that
    # ages naturally; the UI derives "median age" relative to display time.
    median_author_time: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Blame owner over the function's lines.
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_line_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

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

    # Verification (anti-hallucination gate, Phase 1D). Aggregate over the
    # decision's evidence rows: "exact" if any headline field is a verbatim
    # quote of its source span, "fuzzy" if only token-overlap matched,
    # "unverified" if nothing could be grounded.
    verification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified"
    )  # exact | fuzzy | unverified

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


class DecisionEvidence(Base):
    """One verbatim provenance row supporting a :class:`DecisionRecord`.

    Provenance accretes rather than overwrites: when two sources describe the
    same decision they merge into one ``DecisionRecord`` with N evidence rows.
    The decision's headline fields come from the highest-``source_rank`` row;
    its confidence is a function of the best rank plus corroboration count.
    """

    __tablename__ = "decision_evidence"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "source",
            "evidence_file",
            "evidence_commit",
            name="uq_decision_evidence",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    decision_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("decision_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Provenance — which source attested to this decision, and how trusted it is.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # The verbatim span this evidence was drawn from.
    evidence_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Per-evidence confidence + substring-gate verdict.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    verification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified"
    )  # exact | fuzzy | unverified

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class DecisionEdge(Base):
    """A typed, directed edge between two :class:`DecisionRecord` rows.

    The decision graph (Phase 3): decisions are nodes, time/relationships are
    edges. ``kind`` is one of:

    - ``supersedes``     — ``src`` replaces ``dst`` (e.g. JWT supersedes sessions).
    - ``refines``        — ``src`` narrows/extends ``dst`` without reversing it.
    - ``relates_to``     — same topic, no ordering implied.
    - ``conflicts_with`` — two *active* decisions that contradict; neither
      clearly supersedes the other (a governance smell surfaced in health).

    Edges accrete (propose-don't-clobber): a detected supersession always
    records the edge; the older decision's status is only auto-flipped to
    ``superseded`` above a high confidence threshold, leaving everything else a
    reviewable proposal.
    """

    __tablename__ = "decision_edges"
    __table_args__ = (
        UniqueConstraint(
            "src_decision_id",
            "dst_decision_id",
            "kind",
            name="uq_decision_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    src_decision_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("decision_records.id", ondelete="CASCADE"), nullable=False
    )
    dst_decision_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("decision_records.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # supersedes | refines | relates_to | conflicts_with
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )


class DecisionNodeLink(Base):
    """A first-class decision→code link (file or module governed by a decision).

    Promotes the linkage that ``DecisionRecord.affected_files_json`` /
    ``affected_modules_json`` hold as a denormalized cache into rows that are
    indexed on both ``decision_id`` and ``node_id`` — so the graph can be walked
    in either direction (file → governing decisions, decision → governed code).
    Kept in sync from the JSON arrays on every ``bulk_upsert_decisions``.
    """

    __tablename__ = "decision_node_links"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "node_id",
            "link_type",
            name="uq_decision_node_link",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("decision_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    link_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="file"
    )  # file | module
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
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
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now_utc)
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
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class HealthFinding(Base):
    """One biomarker hit produced by the code-health analyzer."""

    __tablename__ = "health_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    biomarker_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    function_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    health_impact: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Health dimension this finding homes under (defect / maintainability /
    # performance). Nullable + no backfill: old rows stay NULL until the next
    # index recomputes them; new writes always set it (defaults to "defect").
    dimension: Mapped[str | None] = mapped_column(String(16), nullable=True, default="defect")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class RefactoringSuggestion(Base):
    """One deterministic refactoring opportunity from the refactoring layer.

    Mirrors the ``RefactoringSuggestion`` dataclass in
    ``analysis/health/refactoring/models.py``. ``plan_json`` /
    ``evidence_json`` / ``blast_radius_json`` carry the structured,
    type-specific payloads (open dicts) so later refactoring types add no
    columns. Written delete-then-insert per repo (or upserted per changed
    file on incremental updates), exactly like ``health_findings``.
    """

    __tablename__ = "refactoring_suggestions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    refactoring_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    target_symbol: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    impact_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    effort_bucket: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    blast_radius_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    source_biomarker: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )


class HealthFileMetric(Base):
    """Per-file aggregate metrics + final score."""

    __tablename__ = "health_file_metrics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    max_ccn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_nesting: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nloc: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplication_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_test_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    line_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    branch_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Three-signal split. ``score`` above stays the overall surfaced number and
    # equals ``defect_score`` until a deliberate blend decision. ``performance_score``
    # is NULL until the performance detectors land. All nullable + no backfill:
    # recompute on the next index repopulates them.
    defect_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    maintainability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )

    __table_args__ = (
        UniqueConstraint("repository_id", "file_path", name="uq_health_file_metrics"),
    )


class HealthSnapshot(Base):
    """KPI history + compact per-file score map. Keep last 50 per repo."""

    __tablename__ = "health_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    hotspot_health: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    average_health: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    worst_performer_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    worst_performer_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    per_file_scores_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class CoverageFile(Base):
    """Per-file coverage data, overwritten on each --coverage run."""

    __tablename__ = "coverage_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    line_coverage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    branch_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    covered_lines_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    total_coverable_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    ingested_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (UniqueConstraint("repository_id", "file_path", name="uq_coverage_files"),)


class TestCoverageEntry(Base):
    """One ``(test, source file)`` coverage fact - the test-to-code map.

    Where :class:`CoverageFile` stores per-file aggregate coverage (a file is
    covered, merged across every test), this keeps the test dimension: a row
    says test ``test_id`` covered ``covered_lines_json`` of ``source_file``.
    It backs the reverse index "given changed lines, which tests hit them"
    that run-only-affected-tests and coverage-backed missing-test signals
    lean on.

    Design: a table, not a graph edge. The first consumer is a CI lookup
    keyed by changed source file + lines, which is a straight table query; a
    projected graph edge (composing with blast-radius) is deferred until a
    consumer needs graph composition.

    Point-in-time only: rows are overwritten per ingest run (delete-then
    -insert by ``repository_id``), no history - mirroring ``CoverageFile``.
    Populated only from context-carrying reports; ``covered_lines_json`` is a
    JSON int list (ceiling: fine at current scale, swap to a bitmap/RLE
    encoding if O(tests x files) row size becomes a problem).
    """

    __tablename__ = "test_coverage"
    # Not a pytest test class despite the ``Test`` prefix.
    __test__ = False

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    # Raw test identifier from the report (coverage.py context
    # ``module::qualname|phase`` or an lcov ``TN:`` name).
    test_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Canonical repo key of the test's own source file, when resolvable.
    test_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Canonical repo key of the covered source file.
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    covered_lines_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    ingested_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        UniqueConstraint("repository_id", "test_id", "source_file", name="uq_test_coverage"),
        # Reverse index (changed source file -> covering tests) is the hot path.
        Index("ix_test_coverage_repo_source", "repository_id", "source_file"),
        # Forward index (test -> files it covers).
        Index("ix_test_coverage_repo_test", "repository_id", "test_id"),
    )


class AnswerCache(Base):
    """Cached LLM-synthesized answers from get_answer.

    Keyed by (repo_id, question_hash). The hash is computed from the
    normalized question text only — answer cache invalidation on index
    change is handled by deleting rows for a repository when its alembic
    head advances (cheap to rebuild).

    Storing payload as a single JSON text column keeps the schema stable
    across get_answer response shape changes.
    """

    __tablename__ = "answer_cache"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256 hex of the normalized (lowercased + stripped) question.
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Original (un-normalized) question, kept for human inspection.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Full JSON payload from get_answer (answer, citations, confidence,
    # fallback_targets, retrieval).
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Provider + model used for the synthesis call (lets us invalidate
    # selectively if a better model is configured later).
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "question_hash", name="uq_answer_cache_q"),)


class KnowledgeGraphLayer(Base):
    __tablename__ = "knowledge_graph_layers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    layer_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    node_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Curated sub-groups within the layer: [{"id", "name", "nodeIds"}].
    sub_groups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "layer_id", name="uq_kg_layer"),)


class KnowledgeGraphTourStep(Base):
    __tablename__ = "knowledge_graph_tour_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    node_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Curated, layer-aware tour fields (empty/None for legacy LLM tours).
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "step_order", name="uq_kg_tour_step"),)


class KnowledgeGraphProjectMeta(Base):
    """Project-level curated KG metadata — one row per repository.

    Holds the ranked entry points surfaced by the curation pass so the server
    never has to read workspace files at request time. JSON columns leave room
    for future project-level curated metadata.
    """

    __tablename__ = "kg_project_meta"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    entry_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    entry_candidates_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", name="uq_kg_project_meta"),)


class KnowledgeGraphNodeMeta(Base):
    """Per-node curated KG metadata (presentation view only).

    Stores the curated ``type``/``summary``/``tags`` for file nodes so the
    architecture view can prefer them over heuristics after the one-time
    file → DB migration. The AST graph's ``graph_nodes`` rows are untouched.
    """

    __tablename__ = "kg_node_meta"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_type: Mapped[str] = mapped_column(Text, nullable=False, default="file")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )

    __table_args__ = (UniqueConstraint("repository_id", "node_id", name="uq_kg_node_meta"),)


class PipelineJob(Base):
    """Checkpoint/resume state for one execution of a pipeline phase.

    Inserted at the start of each phase that opts into checkpointing, then
    updated on a fixed cadence with the latest opaque ``cursor`` value
    (interpreted by the phase implementation — typically a file path,
    commit SHA, or batch index). On startup, the orchestrator queries
    rows in state ``running`` / ``pending`` for the active repo and
    offers to resume them.

    The full orchestrator integration is delivered in a follow-up phase;
    this revision introduces the table + ABC so plugin authors can target
    it.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
