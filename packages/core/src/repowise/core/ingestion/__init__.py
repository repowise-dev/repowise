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

from __future__ import annotations

from typing import TYPE_CHECKING

from ..lazy_exports import lazy_exports
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

if TYPE_CHECKING:
    from .change_detector import AffectedPages, ChangeDetector, FileDiff, SymbolDiff, SymbolRename
    from .graph import GraphBuilder
    from .parser import LANGUAGE_CONFIGS, ASTParser, LanguageConfig, parse_file
    from .traverser import FileTraverser, TraversalStats, is_candidate_source_path
    from .tsconfig_resolver import TsconfigResolver, wire_tsconfig_resolver

# Loaded on first use: the graph builder alone pulls in networkx (~0.8 s), and
# importing any ``ingestion.*`` submodule runs this file, so every read path
# that only wants the models or the language registry paid for the parser.
__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        **dict.fromkeys(
            ("AffectedPages", "ChangeDetector", "FileDiff", "SymbolDiff", "SymbolRename"),
            ".change_detector",
        ),
        "GraphBuilder": ".graph",
        **dict.fromkeys(
            ("LANGUAGE_CONFIGS", "ASTParser", "LanguageConfig", "parse_file"), ".parser"
        ),
        **dict.fromkeys(
            ("FileTraverser", "TraversalStats", "is_candidate_source_path"), ".traverser"
        ),
        **dict.fromkeys(("TsconfigResolver", "wire_tsconfig_resolver"), ".tsconfig_resolver"),
    },
    globals(),
)

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
