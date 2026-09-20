"""repowise ingestion pipeline.

Public surface
--------------
FileTraverser   — traverse a repo, respecting gitignore + blocklist
ASTParser       — unified parser (one class for all languages via .scm files)
parse_file      — module-level convenience wrapper around ASTParser
GraphBuilder    — build a NetworkX dependency graph from ParsedFile objects
ChangeDetector  — git-based change detection + symbol rename detection
LANGUAGE_CONFIGS — dict of per-language configuration
"""

from .change_detector import AffectedPages, ChangeDetector, FileDiff, SymbolDiff, SymbolRename
from .graph import GraphBuilder
from .models import (
    EXTENSION_TO_LANGUAGE,
    CallReceiver,
    CallSite,
    EdgeType,
    FileInfo,
    HeritageRelation,
    Import,
    NamedBinding,
    PackageInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
    SymbolKind,
    compute_content_hash,
)
from .parser import LANGUAGE_CONFIGS, ASTParser, LanguageConfig, parse_file
from .traverser import FileTraverser, TraversalStats, is_candidate_source_path
from .tsconfig_resolver import TsconfigResolver, wire_tsconfig_resolver

__all__ = [
    "EXTENSION_TO_LANGUAGE",
    "LANGUAGE_CONFIGS",
    # Parsing
    "ASTParser",
    # Change detection
    "AffectedPages",
    # Models
    "CallReceiver",
    "CallSite",
    "ChangeDetector",
    "EdgeType",
    "FileDiff",
    "FileInfo",
    # Traversal
    "FileTraverser",
    # Graph
    "GraphBuilder",
    "HeritageRelation",
    "Import",
    "LanguageConfig",
    "NamedBinding",
    "PackageInfo",
    "ParsedFile",
    "RepoStructure",
    "Symbol",
    "SymbolDiff",
    "SymbolKind",
    "SymbolRename",
    "TraversalStats",
    "TsconfigResolver",
    "compute_content_hash",
    "is_candidate_source_path",
    "parse_file",
    "wire_tsconfig_resolver",
]
