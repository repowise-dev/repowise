"""WikiCode persistence layer.

Public API — import from here rather than sub-modules.

Backends
--------
SQLite (default)
    Uses ``aiosqlite`` for async I/O and SQLite FTS5 for full-text search.
    Vector embeddings are stored in :class:`InMemoryVectorStore` by default
    or :class:`LanceDBVectorStore` when the ``search`` extra is installed.

PostgreSQL
    Uses ``asyncpg`` and the ``pgvector`` extension.  Install the
    ``pgvector`` extra: ``pip install wikicode-core[pgvector]``.
"""

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
    get_all_git_metadata,
    get_conversation,
    get_dead_code_findings,
    get_dead_code_summary,
    get_decision,
    get_decision_health_summary,
    get_generation_job,
    get_git_metadata,
    get_git_metadata_bulk,
    get_page,
    get_page_versions,
    get_repository,
    get_repository_by_path,
    get_stale_decisions,
    get_stale_pages,
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
    get_db_url,
    get_session,
    init_db,
)
from .embedder import Embedder, MockEmbedder
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
    "async_sessionmaker",
    "get_db_url",
    "create_engine",
    "create_session_factory",
    "init_db",
    "get_session",
    # models
    "Base",
    "ChatMessage",
    "Conversation",
    "Repository",
    "GenerationJob",
    "Page",
    "PageVersion",
    "GraphNode",
    "GraphEdge",
    "WebhookEvent",
    "WikiSymbol",
    "GitMetadata",
    "DeadCodeFinding",
    "DecisionRecord",
    # crud
    "upsert_repository",
    "get_repository",
    "get_repository_by_path",
    "upsert_generation_job",
    "get_generation_job",
    "update_job_status",
    "upsert_page",
    "upsert_page_from_generated",
    "get_page",
    "list_pages",
    "get_page_versions",
    "get_stale_pages",
    "batch_upsert_graph_nodes",
    "batch_upsert_graph_edges",
    "batch_upsert_symbols",
    "store_webhook_event",
    "mark_webhook_processed",
    # git metadata crud
    "upsert_git_metadata",
    "get_git_metadata",
    "get_git_metadata_bulk",
    "get_all_git_metadata",
    "upsert_git_metadata_bulk",
    # dead code crud
    "save_dead_code_findings",
    "get_dead_code_findings",
    "update_dead_code_status",
    "get_dead_code_summary",
    # decision crud
    "upsert_decision",
    "get_decision",
    "list_decisions",
    "update_decision_status",
    "delete_decision",
    "bulk_upsert_decisions",
    "recompute_decision_staleness",
    "get_stale_decisions",
    "get_decision_health_summary",
    # chat crud
    "create_conversation",
    "get_conversation",
    "list_conversations",
    "update_conversation_title",
    "delete_conversation",
    "touch_conversation",
    "create_chat_message",
    "list_chat_messages",
    "count_chat_messages",
    # embedder
    "Embedder",
    "MockEmbedder",
    # vector store
    "VectorStore",
    "InMemoryVectorStore",
    "LanceDBVectorStore",
    "PgVectorStore",
    # search
    "FullTextSearch",
    "SearchResult",
]
