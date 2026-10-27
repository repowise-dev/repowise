"""repowise persistence layer.

Public API — import from here rather than sub-modules.

Backends
--------
SQLite (default)
    Uses ``aiosqlite`` for async I/O and SQLite FTS5 for full-text search.
    Vector embeddings are stored in :class:`InMemoryVectorStore` by default
    or :class:`LanceDBVectorStore` when the ``search`` extra is installed.

PostgreSQL
    Uses ``asyncpg``.  Install the ``postgres`` extra:
    ``pip install "repowise[postgres]"``.  Semantic search still uses
    LanceDB on this backend; the ``wiki_pages.embedding`` pgvector column
    added by the migration is not read or written yet (see #3090).

``Embedder`` and ``MockEmbedder`` are imported eagerly; every other name loads
its submodule on first use, so importing one submodule (``persistence.database``
for a freshness stamp) does not pay for every CRUD module.
"""

from __future__ import annotations

import importlib
from typing import Any

from repowise.core.providers.embedding.base import Embedder, MockEmbedder

#: Submodule -> the names it exports lazily from this package.
_LAZY_BY_MODULE = {
    "crud": (
        "batch_upsert_graph_edges",
        "batch_upsert_graph_metrics",
        "batch_upsert_graph_node_membership",
        "batch_upsert_graph_nodes",
        "batch_upsert_symbols",
        "bulk_upsert_decisions",
        "bulk_upsert_external_systems",
        "count_chat_messages",
        "create_chat_message",
        "create_conversation",
        "delete_conversation",
        "delete_decision",
        "delete_repository",
        "get_all_file_metrics",
        "get_all_git_metadata",
        "get_all_graph_edges",
        "get_all_graph_nodes",
        "get_community_members",
        "get_conversation",
        "get_cross_community_edges",
        "get_dead_code_findings",
        "get_dead_code_git_fields",
        "get_dead_code_summary",
        "get_decision",
        "get_decision_health_summary",
        "get_generation_job",
        "get_git_metadata",
        "get_git_metadata_bulk",
        "get_graph_edges_for_node",
        "get_graph_metrics",
        "get_graph_node",
        "get_graph_nodes_by_ids",
        "get_kg_layers",
        "get_kg_node_meta",
        "get_kg_project_meta",
        "get_kg_tour_steps",
        "get_node_degree_counts",
        "get_page",
        "get_page_versions",
        "get_repository",
        "get_repository_by_path",
        "get_scc_members",
        "get_stale_decisions",
        "get_stale_file_page_ages",
        "get_stale_pages",
        "get_stale_structural_file_paths",
        "get_stale_update_targets",
        "get_top_entry_points",
        "link_graph_nodes_to_external_systems",
        "list_chat_messages",
        "list_conversations",
        "list_decision_evidence",
        "list_decisions",
        "list_external_systems",
        "list_page_ids",
        "list_pages",
        "load_prior_pages",
        "load_stale_structural_file_paths",
        "load_stale_update_targets",
        "mark_webhook_processed",
        "recompute_decision_staleness",
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
        "upsert_kg_layers",
        "upsert_kg_node_meta",
        "upsert_kg_project_meta",
        "upsert_kg_tour_steps",
        "upsert_page",
        "upsert_page_from_generated",
        "upsert_pages_from_generated",
        "upsert_repository",
    ),
    "database": (
        "AsyncEngine",
        "AsyncSession",
        "async_sessionmaker",
        "create_engine",
        "create_session_factory",
        "get_configured_db_url",
        "get_db_url",
        "get_repo_db_path",
        "get_session",
        "init_db",
        "refresh_planner_stats",
        "resolve_db_url",
    ),
    "decision_graph": (
        "build_lineage_chain",
        "get_decision_edges",
        "get_governed_nodes",
        "get_governing_decisions",
        "list_conflict_edges",
        "sync_decision_node_links",
        "upsert_decision_edge",
    ),
    "models": (
        "Base",
        "ChatMessage",
        "Conversation",
        "DeadCodeFinding",
        "DecisionEdge",
        "DecisionEvidence",
        "DecisionNodeLink",
        "DecisionRecord",
        "DocDriftFinding",
        "ExternalSystem",
        "GenerationJob",
        "GitCommit",
        "GitMetadata",
        "GraphEdge",
        "GraphMetric",
        "GraphNode",
        "GraphNodeMembership",
        "KnowledgeGraphLayer",
        "KnowledgeGraphNodeMeta",
        "KnowledgeGraphProjectMeta",
        "KnowledgeGraphTourStep",
        "Page",
        "PageVersion",
        "Repository",
        "WebhookEvent",
        "WikiSymbol",
    ),
    "search": (
        "FullTextSearch",
        "SearchResult",
    ),
    "vector_store": (
        "InMemoryVectorStore",
        "LanceDBVectorStore",
        "PgVectorStore",
        "VectorStore",
    ),
}
#: Lazily exported name -> its submodule.
_LAZY = {name: module for module, names in _LAZY_BY_MODULE.items() for name in names}


def __getattr__(name: str) -> Any:
    if name in _LAZY_BY_MODULE:
        # The eager imports bound these submodules as attributes.
        return importlib.import_module(f".{name}", __name__)
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    # database
    "AsyncEngine",
    "AsyncSession",
    # models
    "Base",
    "ChatMessage",
    "Conversation",
    "DeadCodeFinding",
    "DecisionEdge",
    "DecisionEvidence",
    "DecisionNodeLink",
    "DecisionRecord",
    "DocDriftFinding",
    # embedder
    "Embedder",
    "ExternalSystem",
    # search
    "FullTextSearch",
    "GenerationJob",
    "GitCommit",
    "GitMetadata",
    "GraphEdge",
    "GraphMetric",
    "GraphNode",
    "GraphNodeMembership",
    # vector store
    "InMemoryVectorStore",
    # knowledge graph
    "KnowledgeGraphLayer",
    "KnowledgeGraphNodeMeta",
    "KnowledgeGraphProjectMeta",
    "KnowledgeGraphTourStep",
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
    "batch_upsert_graph_metrics",
    "batch_upsert_graph_node_membership",
    "batch_upsert_graph_nodes",
    "batch_upsert_symbols",
    # decision graph (edges + node links + lineage)
    "build_lineage_chain",
    # decision crud
    "bulk_upsert_decisions",
    # external systems crud (C4 L1)
    "bulk_upsert_external_systems",
    # chat crud
    "count_chat_messages",
    "create_chat_message",
    "create_conversation",
    "create_engine",
    "create_session_factory",
    "delete_conversation",
    "delete_decision",
    "delete_repository",
    # graph read-side queries
    "get_all_file_metrics",
    # git metadata crud
    "get_all_git_metadata",
    "get_all_graph_edges",
    "get_all_graph_nodes",
    "get_community_members",
    "get_configured_db_url",
    "get_conversation",
    "get_cross_community_edges",
    "get_db_url",
    # dead code crud
    "get_dead_code_findings",
    "get_dead_code_git_fields",
    "get_dead_code_summary",
    "get_decision",
    "get_decision_edges",
    "get_decision_health_summary",
    "get_generation_job",
    "get_git_metadata",
    "get_git_metadata_bulk",
    "get_governed_nodes",
    "get_governing_decisions",
    "get_graph_edges_for_node",
    "get_graph_metrics",
    "get_graph_node",
    "get_graph_nodes_by_ids",
    # knowledge graph crud
    "get_kg_layers",
    "get_kg_node_meta",
    "get_kg_project_meta",
    "get_kg_tour_steps",
    "get_node_degree_counts",
    "get_page",
    "get_page_versions",
    "get_repo_db_path",
    "get_repository",
    "get_repository_by_path",
    "get_scc_members",
    "get_session",
    "get_stale_decisions",
    "get_stale_file_page_ages",
    "get_stale_pages",
    "get_stale_structural_file_paths",
    "get_stale_update_targets",
    "get_top_entry_points",
    "init_db",
    "link_graph_nodes_to_external_systems",
    "list_chat_messages",
    "list_conflict_edges",
    "list_conversations",
    "list_decision_evidence",
    "list_decisions",
    "list_external_systems",
    "list_page_ids",
    "list_pages",
    "load_prior_pages",
    "load_stale_structural_file_paths",
    "load_stale_update_targets",
    "mark_webhook_processed",
    "recompute_decision_staleness",
    "refresh_planner_stats",
    "resolve_db_url",
    "save_dead_code_findings",
    "store_webhook_event",
    "sync_decision_node_links",
    "touch_conversation",
    "update_conversation_title",
    "update_dead_code_status",
    "update_decision_status",
    "update_job_status",
    "upsert_decision",
    "upsert_decision_edge",
    "upsert_generation_job",
    "upsert_git_metadata",
    "upsert_git_metadata_bulk",
    "upsert_kg_layers",
    "upsert_kg_node_meta",
    "upsert_kg_project_meta",
    "upsert_kg_tour_steps",
    "upsert_page",
    "upsert_page_from_generated",
    "upsert_pages_from_generated",
    "upsert_repository",
]
