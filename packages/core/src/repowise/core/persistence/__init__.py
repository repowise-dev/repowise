"""repowise persistence layer.

Public API — import from here rather than sub-modules.

Backends
--------
SQLite (default)
    Uses ``aiosqlite`` for async I/O and SQLite FTS5 for full-text search.
    Vector embeddings are stored in :class:`InMemoryVectorStore` by default
    or :class:`LanceDBVectorStore` when the ``search`` extra is installed.

PostgreSQL
    Uses ``asyncpg`` and the ``pgvector`` extension.  Install the
    ``pgvector`` extra: ``pip install repowise-core[pgvector]``.
"""

from repowise.core.providers.embedding.base import Embedder, MockEmbedder

from .crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    batch_upsert_symbols,
    bulk_upsert_decisions,
    count_chat_messages,
    create_chat_message,
    create_conversation,
    delete_conversation,
    delete_decision,
    get_all_file_metrics,
    get_all_git_metadata,
    get_community_members,
    get_conversation,
    get_cross_community_edges,
    get_dead_code_findings,
    get_dead_code_summary,
    get_decision,
    get_decision_health_summary,
    get_generation_job,
    get_git_metadata,
    get_git_metadata_bulk,
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
    get_node_degree_counts,
    get_page,
    get_page_versions,
    get_repository,
    get_repository_by_path,
    get_stale_decisions,
    get_stale_pages,
    get_top_entry_points,
    list_chat_messages,
    list_conversations,
    list_decisions,
    list_pages,
    mark_webhook_processed,
    recompute_decision_staleness,
    save_dead_code_findings,
    store_webhook_event,
    touch_conversation,
    update_conversation_title,
    update_dead_code_status,
    update_decision_status,
    update_job_status,
    upsert_decision,
    upsert_generation_job,
    upsert_git_metadata,
    upsert_git_metadata_bulk,
    upsert_page,
    upsert_page_from_generated,
    upsert_repository,
)
from .database import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_engine,
    create_session_factory,
    get_configured_db_url,
    get_db_url,
    get_repo_db_path,
    get_session,
    init_db,
    resolve_db_url,
)
from .models import (
    Base,
    ChatMessage,
    Conversation,
    DeadCodeFinding,
    DecisionRecord,
    GenerationJob,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    PageVersion,
    Repository,
    WebhookEvent,
    WikiSymbol,
)
from .search import FullTextSearch, SearchResult
from .vector_store import (
    InMemoryVectorStore,
    LanceDBVectorStore,
    PgVectorStore,
    VectorStore,
)

__all__ = [
    # database
    "AsyncEngine",
    "AsyncSession",
    # models
    "Base",
    "ChatMessage",
    "Conversation",
    "DeadCodeFinding",
    "DecisionRecord",
    # embedder
    "Embedder",
    # search
    "FullTextSearch",
    "GenerationJob",
    "GitMetadata",
    "GraphEdge",
    "GraphNode",
    # vector store
    "InMemoryVectorStore",
    "LanceDBVectorStore",
    "MockEmbedder",
    "Page",
    "PageVersion",
    "PgVectorStore",
    "Repository",
    "SearchResult",
    "VectorStore",
    "WebhookEvent",
    "WikiSymbol",
    "async_sessionmaker",
    # crud
    "batch_upsert_graph_edges",
    "batch_upsert_graph_nodes",
    "batch_upsert_symbols",
    # decision crud
    "bulk_upsert_decisions",
    # chat crud
    "count_chat_messages",
    # graph read-side queries
    "get_all_file_metrics",
    "get_community_members",
    "get_cross_community_edges",
    "get_graph_edges_for_node",
    "get_graph_node",
    "get_graph_nodes_by_ids",
    "get_node_degree_counts",
    "get_top_entry_points",
    "create_chat_message",
    "create_conversation",
    "create_engine",
    "create_session_factory",
    "delete_conversation",
    "delete_decision",
    # git metadata crud
    "get_all_git_metadata",
    "get_configured_db_url",
    "get_conversation",
    "get_db_url",
    # dead code crud
    "get_dead_code_findings",
    "get_dead_code_summary",
    "get_decision",
    "get_decision_health_summary",
    "get_generation_job",
    "get_git_metadata",
    "get_git_metadata_bulk",
    "get_page",
    "get_page_versions",
    "get_repo_db_path",
    "get_repository",
    "get_repository_by_path",
    "get_session",
    "get_stale_decisions",
    "get_stale_pages",
    "init_db",
    "list_chat_messages",
    "list_conversations",
    "list_decisions",
    "list_pages",
    "mark_webhook_processed",
    "recompute_decision_staleness",
    "resolve_db_url",
    "save_dead_code_findings",
    "store_webhook_event",
    "touch_conversation",
    "update_conversation_title",
    "update_dead_code_status",
    "update_decision_status",
    "update_job_status",
    "upsert_decision",
    "upsert_generation_job",
    "upsert_git_metadata",
    "upsert_git_metadata_bulk",
    "upsert_page",
    "upsert_page_from_generated",
    "upsert_repository",
]
