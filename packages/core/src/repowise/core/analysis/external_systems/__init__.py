"""Session-free folds behind the external-dependency surface.

The aggregation these modules perform used to be SQL fused to a live
``AsyncSession`` in ``repowise.server.services``, which meant any second
consumer — an indexer precomputing artifacts, a deployment without the graph
tables — had to reimplement the bounds and the truncation flags and could
disagree with the product about what a page was not showing. Taking plain
records instead of a session leaves one owner for those semantics.

The server layer keeps the queries; it fetches rows and hands them here.
"""

from __future__ import annotations

from .links import (
    EXTERNAL_NODE_PREFIX,
    ExternalSystemLink,
    build_declaration_index,
    build_declaration_links,
    declaration_name_candidates,
    resolve_declaration,
)
from .records import (
    AUXILIARY_PREFIXES,
    IMPORT_EDGE_TYPE,
    Scope,
    in_scope,
    is_primary_path,
    package_key,
    split_package_key,
    target_basis,
)
from .registry import PackageRegistry, RegistryEntry, build_registry
from .relationships import (
    DEFAULT_FILE_LIMIT,
    DEFAULT_RELATIONSHIP_EDGE_LIMIT,
    DEFAULT_RELATIONSHIP_NODE_LIMIT,
    EVIDENCE_TARGET_LIMIT,
    EXTERNAL_TARGET_LIMIT,
    GraphTarget,
    ImportingFile,
    ImportingFiles,
    RelationshipEdge,
    RelationshipGraph,
    RelationshipNode,
    build_importing_files,
    build_relationship_graph,
    community_key,
    resolve_targets,
    split_community_key,
)
from .summary import (
    DEFAULT_SUMMARY_LIMIT,
    SUMMARY_VERSION_LIMIT,
    PackageEntry,
    PackageSummary,
    build_package_summary,
)

__all__ = [
    "AUXILIARY_PREFIXES",
    "DEFAULT_FILE_LIMIT",
    "DEFAULT_RELATIONSHIP_EDGE_LIMIT",
    "DEFAULT_RELATIONSHIP_NODE_LIMIT",
    "DEFAULT_SUMMARY_LIMIT",
    "EVIDENCE_TARGET_LIMIT",
    "EXTERNAL_NODE_PREFIX",
    "EXTERNAL_TARGET_LIMIT",
    "IMPORT_EDGE_TYPE",
    "SUMMARY_VERSION_LIMIT",
    "ExternalSystemLink",
    "GraphTarget",
    "ImportingFile",
    "ImportingFiles",
    "PackageEntry",
    "PackageRegistry",
    "PackageSummary",
    "RegistryEntry",
    "RelationshipEdge",
    "RelationshipGraph",
    "RelationshipNode",
    "Scope",
    "build_declaration_index",
    "build_declaration_links",
    "build_importing_files",
    "build_package_summary",
    "build_registry",
    "build_relationship_graph",
    "community_key",
    "declaration_name_candidates",
    "in_scope",
    "is_primary_path",
    "package_key",
    "resolve_declaration",
    "resolve_targets",
    "split_community_key",
    "split_package_key",
    "target_basis",
]
