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
    CallSite,
    EdgeType,
    FileInfo,
    Import,
    PackageInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
    SymbolKind,
    compute_content_hash,
)
from .parser import LANGUAGE_CONFIGS, ASTParser, LanguageConfig, parse_file
from .traverser import FileTraverser, TraversalStats
from .tsconfig_resolver import TsconfigResolver

__all__ = [
    # Models
    "CallSite",
    "EdgeType",
    "EXTENSION_TO_LANGUAGE",
    "LANGUAGE_CONFIGS",
    # Parsing
    "ASTParser",
    # Change detection
    "AffectedPages",
    "ChangeDetector",
    "FileDiff",
    "FileInfo",
    # Traversal
    "FileTraverser",
    "TraversalStats",
    # Graph
    "GraphBuilder",
    "TsconfigResolver",
    "Import",
    "LanguageConfig",
    "PackageInfo",
    "ParsedFile",
    "RepoStructure",
    "Symbol",
    "SymbolDiff",
    "SymbolKind",
    "SymbolRename",
    "compute_content_hash",
    "parse_file",
]
