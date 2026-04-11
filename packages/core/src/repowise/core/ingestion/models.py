"""Shared data models for the ingestion pipeline.

These are plain dataclasses (not Pydantic) for speed — the pipeline may process
tens of thousands of files and the overhead of Pydantic validation would add up.
All types are immutable where possible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Language tags
# ---------------------------------------------------------------------------

LanguageTag = Literal[
    "python",
    "typescript",
    "javascript",
    "go",
    "rust",
    "java",
    "cpp",
    "c",
    "csharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "shell",
    "yaml",
    "json",
    "toml",
    "proto",
    "graphql",
    "terraform",
    "dockerfile",
    "makefile",
    "markdown",
    "sql",
    "openapi",
    "unknown",
]

# ---------------------------------------------------------------------------
# Extension → language map (used by FileTraverser and ASTParser)
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, LanguageTag] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".proto": "proto",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".tf": "terraform",
    ".hcl": "terraform",
    ".md": "markdown",
    ".mdx": "markdown",
    ".sql": "sql",
}

SPECIAL_FILENAMES: dict[str, LanguageTag] = {
    "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "Makefile": "makefile",
    "makefile": "makefile",
    "GNUmakefile": "makefile",
}

# ---------------------------------------------------------------------------
# Symbol kinds
# ---------------------------------------------------------------------------

SymbolKind = Literal[
    "function",
    "class",
    "method",
    "interface",
    "enum",
    "constant",
    "type_alias",
    "decorator",
    "trait",
    "impl",
    "struct",
    "module",
    "macro",
    "variable",
]

# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """Metadata about a single source file discovered during traversal."""

    path: str  # POSIX path relative to repo root
    abs_path: str  # absolute filesystem path
    language: LanguageTag
    size_bytes: int
    git_hash: str  # SHA of last commit touching this file (empty if unavailable)
    last_modified: datetime
    is_test: bool
    is_config: bool
    is_api_contract: bool
    is_entry_point: bool


@dataclass
class PackageInfo:
    """A sub-package/workspace within a monorepo."""

    name: str
    path: str  # POSIX path relative to repo root
    language: LanguageTag
    entry_points: list[str]
    manifest_file: str  # pyproject.toml | package.json | Cargo.toml | go.mod


@dataclass
class RepoStructure:
    """High-level structure of a repository."""

    is_monorepo: bool
    packages: list[PackageInfo]
    root_language_distribution: dict[str, float]  # {"python": 0.45, ...}
    total_files: int
    total_loc: int
    entry_points: list[str]


@dataclass
class Symbol:
    """A code symbol (function, class, method, …) extracted from a file."""

    id: str  # "<rel_path>::<name>" or "<rel_path>::<class>::<method>"
    name: str
    qualified_name: str  # dotted full name, e.g. "myapp.calc.Calculator.add"
    kind: SymbolKind
    signature: str  # full signature string
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    docstring: str | None
    decorators: list[str] = field(default_factory=list)
    visibility: Literal["public", "private", "protected", "internal"] = "public"
    is_async: bool = False
    complexity_estimate: int = 1  # cyclomatic complexity
    language: str = ""
    parent_name: str | None = None  # for methods: the containing class name


@dataclass
class Import:
    """An import statement extracted from a source file."""

    raw_statement: str
    module_path: str  # normalized module path
    imported_names: list[str]  # specific names, or ["*"] for wildcard
    is_relative: bool
    resolved_file: str | None  # absolute path if successfully resolved


@dataclass
class CallSite:
    """A function or method call extracted from a source file.

    Used by GraphBuilder to create CALLS edges between symbol nodes.
    """

    target_name: str  # function/method name being called
    receiver_name: str | None  # object/class for method calls (e.g. "user" in user.save())
    caller_symbol_id: str | None  # enclosing symbol ID (e.g. "src/app.py::main")
    line: int  # 1-indexed line number of the call
    argument_count: int | None  # number of arguments (None if unknown)


# ---------------------------------------------------------------------------
# Edge types used in the symbol-level dependency graph
# ---------------------------------------------------------------------------

EdgeType = Literal[
    "imports",
    "defines",
    "calls",
    "has_method",
    "has_property",
    "extends",
    "implements",
    "method_overrides",
    "method_implements",
    "co_changes",
    "framework",
    "dynamic",
]


@dataclass
class ParsedFile:
    """Full result of parsing a single source file."""

    file_info: FileInfo
    symbols: list[Symbol]
    imports: list[Import]
    exports: list[str]  # names exported by this file
    calls: list[CallSite] = field(default_factory=list)
    docstring: str | None = None  # module/file-level docstring
    parse_errors: list[str] = field(default_factory=list)  # non-fatal parser warnings/errors
    content_hash: str = ""  # SHA-256 hex of raw file bytes


def compute_content_hash(source: bytes) -> str:
    """Return the SHA-256 hex digest of *source*."""
    return hashlib.sha256(source).hexdigest()
