"""Shared data models for the ingestion pipeline.

These are plain dataclasses (not Pydantic) for speed — the pipeline may process
tens of thousands of files and the overhead of Pydantic validation would add up.
All types are immutable where possible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

from .languages.registry import REGISTRY as _REGISTRY

# ---------------------------------------------------------------------------
# Language tags
# ---------------------------------------------------------------------------

LanguageTag = Literal[
    "python",
    "typescript",
    "javascript",
    # Single-file components — parsed as TypeScript via sfc_source.
    "svelte",
    "vue",
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
    "luau",
    "dart",
    "pascal",
    # Passthrough code languages (no AST parser yet — empty ParsedFile,
    # files enter the graph via the generic resolver). Before these tags
    # existed the traverser silently skipped such files as unknown, so e.g.
    # an Elixir repo indexed only its yaml/markdown.
    "elixir",
    "erlang",
    "clojure",
    "haskell",
    "lean",
    "ocaml",
    "fsharp",
    "crystal",
    "nim",
    "dlang",
    "elm",
    "zig",
    "objectivec",
    "julia",
    "r",
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
    "asciidoc",
    "sql",
    "openapi",
    "xaml",
    # Markup with no symbols, but <script src>/<link href> are real edges.
    "html",
    "unknown",
]

# ---------------------------------------------------------------------------
# Extension → language map (used by FileTraverser and ASTParser)
#
# Derived from the centralised LanguageRegistry.  Only the extensions
# known to the original LanguageTag set are included here — the registry
# also covers extra git-blame-only languages.
# ---------------------------------------------------------------------------

_LANGUAGE_TAG_VALUES: frozenset[str] = frozenset(get_args(LanguageTag))

EXTENSION_TO_LANGUAGE: dict[str, LanguageTag] = {
    ext: tag  # type: ignore[misc]
    for ext, tag in _REGISTRY.all_extensions().items()
    if tag in _LANGUAGE_TAG_VALUES
}

SPECIAL_FILENAMES: dict[str, LanguageTag] = {
    fn: tag  # type: ignore[misc]
    for fn, tag in _REGISTRY.all_special_filenames().items()
    if tag in _LANGUAGE_TAG_VALUES
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
    # True when a language-level export marker is present (e.g. C/C++
    # ``__declspec(dllexport)`` / ``__attribute__((visibility("default")))``).
    # Used by dead-code analysis to whitelist exported entry points.
    is_exported_symbol: bool = False
    # True when this record is a bodiless declaration — a C/C++ forward
    # declaration in a header. The definition carrying the same name lives in
    # a .cpp and is the symbol a call should attach to; the call resolver
    # redirects onto it, and the dead-code pass never reports a declaration,
    # since a declaration is not independently deletable.
    is_declaration: bool = False


@dataclass
class NamedBinding:
    """One resolved name from an import statement.

    Tracks the local alias, the original exported name, and the resolved
    source file so that call resolution can map aliases back to symbols.
    """

    local_name: str  # name used in calling file (e.g., "np", "Calc")
    exported_name: str | None  # original name in source (None for module aliases)
    source_file: str | None  # resolved file path (populated during graph build)
    is_module_alias: bool = False  # True for "import x" / "import * as ns"
    is_global: bool = False  # C# `global using` — applies to every file in the project
    is_static_import: bool = False  # C# `using static` / Java static import — pulls members


@dataclass
class Import:
    """An import statement extracted from a source file."""

    raw_statement: str
    module_path: str  # normalized module path
    imported_names: list[str]  # specific names, or ["*"] for wildcard
    is_relative: bool
    resolved_file: str | None  # absolute path if successfully resolved
    bindings: list[NamedBinding] = field(default_factory=list)
    is_reexport: bool = False  # True for `pub use` (Rust) or re-export patterns


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


HeritageKind = Literal["extends", "implements", "trait_impl", "mixin"]


@dataclass
class HeritageRelation:
    """A class/struct/impl inheritance or interface implementation relationship.

    Extracted from AST class definitions. Resolved to graph edges by
    HeritageResolver during the graph build phase.
    """

    child_name: str  # the class/struct being defined
    parent_name: str  # superclass, interface, or trait name
    kind: HeritageKind  # relationship type
    line: int  # 1-indexed line of the class definition


# ---------------------------------------------------------------------------
# Edge types used in the symbol-level dependency graph
# ---------------------------------------------------------------------------

# The vocabulary is exhaustive and *true*: every member is emitted by some
# producer, and no producer emits anything outside it. Both halves are load
# bearing. A declared-but-never-emitted member is what let a dozen consumers
# each write a set against a type that does not exist, and an emitted-but-
# undeclared type is what made every one of those sets silently incomplete.
#
# `tests/unit/ingestion/test_edge_type_vocabulary.py` holds both directions
# shut: it AST-walks every `add_edge` call in the packages and fails on a
# literal that is not a member here.
#
# Removed 2026-08-14, each measured at 0 rows across 42 local indexes with no
# producer anywhere in the tree: `has_property` (only `defines` and
# `has_method` are emitted for containment), `method_overrides` (a
# `HeritageKind`, never an edge type — the TS mirror in
# `packages/types/src/symbols.ts` still declares it as a heritage kind, which
# is correct), and bare `dynamic`.
EdgeType = Literal[
    "imports",
    "defines",
    "calls",
    "has_method",
    "extends",
    "implements",
    "method_implements",
    "co_changes",
    "framework",
    # A data reference rather than a call. Two `_add_reads_edge` helpers emit
    # it at different layers: C# member access file → file, Express
    # route/middleware wiring symbol → symbol.
    "reads",
    # Dynamic-dispatch hints. `dynamic_hints` extractors emit a `DynamicKind`
    # sub-type and `EdgesMixin.add_dynamic_edges` prefixes it, so `url_route`
    # reaches the graph as `dynamic_url_route` and never as itself. Consumers
    # matching bare `"dynamic"` — three of them did — match none of these.
    "dynamic_uses",
    "dynamic_imports",
    "dynamic_url_route",
    # Synthesised file-to-file edge emitted from constructor / method /
    # delegate / record parameter type references in statically-typed
    # languages (currently C#; see type_ref_resolution.py). Distinct
    # from ``imports`` so analyses can weight it lower and so the
    # persistence layer can surface provenance for these edges.
    "type_use",
]

# Runtime mirror of the Literal, for the places that must test membership
# rather than annotate. Derived, never hand-written — a second hand-written
# copy is the exact failure this phase exists to remove.
EDGE_TYPE_VALUES: frozenset[str] = frozenset(get_args(EdgeType))

# What a dynamic-hint extractor reports, *before* `add_dynamic_edges` prefixes
# it. Deliberately a separate vocabulary from `EdgeType`: `url_route` is a
# legal hint kind and never a legal graph edge type, and conflating the two is
# what put `url_route` in the plan for this phase as an edge type to declare
# when the graph has never held one.
DynamicKind = Literal["dynamic_uses", "dynamic_imports", "url_route"]

# Structural containment, not reference. ``defines`` is file → symbol and
# ``has_method`` is class symbol → member symbol, so both endpoints describe
# the same code rather than one depending on the other. Their target is a
# ``path::Name`` symbol node, which is why a consumer that keys on file paths
# gets nothing usable out of them.
CONTAINMENT_EDGE_TYPES: frozenset[str] = frozenset({"defines", "has_method"})

# Evidence from history rather than from code: two files move together in
# commits, not one referencing the other. This is the one that bites, because
# a co-change edge fed back in as a dependency makes every co-change partner
# look like an import of its own subject.
TEMPORAL_EDGE_TYPES: frozenset[str] = frozenset({"co_changes"})

# Edge types that are *not* code dependencies, and so must be excluded by any
# consumer answering "what depends on this?" about FILES.
#
# Excluding containment is right for that question and wrong for traversal.
# Containment is the only bridge between the two layers of the graph: files are
# joined to each other by ``imports`` / ``type_use``, symbols to each other by
# ``calls`` / ``extends`` / ``implements``, and nothing points from a symbol
# back to a file. Drop ``defines`` and a caller can no longer walk from a file
# to the functions it declares, so anything traversing the graph wants
# ``TEMPORAL_EDGE_TYPES`` and a ``node_type`` check instead.
NON_DEPENDENCY_EDGE_TYPES: frozenset[str] = CONTAINMENT_EDGE_TYPES | TEMPORAL_EDGE_TYPES

# ---------------------------------------------------------------------------
# The named views. Ask for the one that matches your question.
# ---------------------------------------------------------------------------
#
# Before these existed, thirteen modules each kept a private set answering some
# version of "which edges count as a dependency", and no two were identical:
# `type_use` was a dependency in five and absent from four, `reads` appeared in
# one of thirteen, and three tested for a bare `"dynamic"` that no producer has
# ever emitted — so they matched none of the 6,153 real `dynamic_*` edges.
#
# The split below is not a taste call. Measured across 42 local indexes, every
# edge type lands on one side 100% of the time, with a single exception noted
# on `reads`. Add a member here, not a set of your own, and say which view you
# want at the call site.

# File → file code references: static imports, C#-style type references,
# synthesised framework wiring, and dispatch the parser cannot see statically.
# This is the right view for anything building a *file*-level subgraph —
# communities, dependency cycles, coupling. Note what is absent: `co_changes`
# is history rather than code, and symbol-level types put both endpoints on
# nodes a file-keyed consumer cannot resolve.
FILE_DEPENDENCY_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "imports",
        "type_use",
        "framework",
        "dynamic_uses",
        "dynamic_imports",
        "dynamic_url_route",
        # C# member access (`var x = new T(); x.Prop`) resolves to the file
        # declaring the type, so this is a real file-level reference. See the
        # note on SYMBOL_USE_EDGE_TYPES: `reads` is emitted at both layers.
        "reads",
    }
)

# Symbol → symbol references. "Something reaches this symbol", so containment
# is excluded: a class containing a method is not the method being used.
#
# `reads` is the one type that spans both layers, which is why it is the one
# member of both sets. Two extractors share the name: `csharp_member_reads`
# emits it file → file (596 rows locally) and `framework_edges/express` emits
# it symbol → symbol from a `path::__module__` node (1 row).
SYMBOL_USE_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "calls",
        "extends",
        "implements",
        "method_implements",
        "reads",
    }
)


# "Does anything use this symbol at all?" — the reachability view. Adds
# ``type_use`` to the symbol set: a C#-style type reference is a real use, and
# the dead-code analyzer and the C/C++ reachability pass both asked for exactly
# this union with their own private copy.
REACHABILITY_USE_EDGE_TYPES: frozenset[str] = SYMBOL_USE_EDGE_TYPES | {"type_use"}


def is_dynamic_edge(edge_type: str | None) -> bool:
    """Whether *edge_type* is a dynamic-dispatch hint.

    Prefix-matched on purpose. The sub-type is open — ``add_dynamic_edges``
    mints ``dynamic_<kind>`` from whatever a hint extractor reports — so a set
    membership test here is what goes stale when a new hint kind lands, and
    three separate consumers matching a bare ``"dynamic"`` is how that failure
    already shipped. The trailing underscore is load bearing: without it a
    future ``dynamically_*`` type would match by accident.
    """
    return edge_type is not None and edge_type.startswith("dynamic_")


@dataclass
class TypeReference:
    """A non-import type reference extracted from a source file.

    Captures usages like constructor parameter types and method parameter
    types where the referenced type is named but not imported through a
    statement the resolver already handles. Resolved to file-level
    ``imports`` edges by the graph builder.
    """

    type_name: str  # head identifier (e.g. "IBasketService" from "IBasketService<T>")
    line: int  # 1-indexed source line
    origin: Literal[
        "ctor_param",
        "method_param",
        "delegate_param",  # C#
        "param_type",
        "field_type",
        "composite_literal",  # Go
        "return_type",
        "type_alias",
        "generic_constraint",  # TS/JS
        "extends",
        "implements",  # TS heritage clauses (file-level type_use)
    ] = "ctor_param"


@dataclass
class ParsedFile:
    """Full result of parsing a single source file."""

    file_info: FileInfo
    symbols: list[Symbol]
    imports: list[Import]
    exports: list[str]  # names exported by this file
    calls: list[CallSite] = field(default_factory=list)
    heritage: list[HeritageRelation] = field(default_factory=list)
    docstring: str | None = None  # module/file-level docstring
    parse_errors: list[str] = field(default_factory=list)  # non-fatal parser warnings/errors
    content_hash: str = ""  # SHA-256 hex of raw file bytes
    type_refs: list[TypeReference] = field(default_factory=list)
    # Top-level symbol names referenced elsewhere in this same file in a
    # non-import, non-call position (callable passed as an argument, used as
    # a type annotation, decorator target, default value, …). Populated for
    # Python only; lets the dead-code unused-export pass rescue symbols whose
    # only use is intra-module. See ``python_local_refs``.
    local_refs: frozenset[str] = field(default_factory=frozenset)


def compute_content_hash(source: bytes) -> str:
    """Return the SHA-256 hex digest of *source*."""
    return hashlib.sha256(source).hexdigest()
