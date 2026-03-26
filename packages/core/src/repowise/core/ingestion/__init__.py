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
from .traverser import FileTraverser

__all__ = [
    # Traversal
    "FileTraverser",
    # Parsing
    "ASTParser",
    "parse_file",
    "LANGUAGE_CONFIGS",
    "LanguageConfig",
    # Graph
    "GraphBuilder",
    # Change detection
    "ChangeDetector",
    "FileDiff",
    "SymbolDiff",
    "SymbolRename",
    "AffectedPages",
    # Models
    "FileInfo",
    "Import",
    "PackageInfo",
    "ParsedFile",
    "RepoStructure",
    "Symbol",
    "SymbolKind",
    "EXTENSION_TO_LANGUAGE",
    "compute_content_hash",
]
