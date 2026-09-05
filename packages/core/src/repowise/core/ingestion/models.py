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
    # Razor / Blazor markup, projected to C# via sfc_source (byte-scan).
    "razor",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "luau",
    "dart",
    "pascal",
    "gdscript",
    "vbnet",
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
    # Lightweight: qmldir-declared module imports and quoted references.
    "qml",
    # Godot .tscn/.tres/.escn + project.godot: data with no symbols, but
    # [ext_resource path=...] and [autoload] are how a Godot project reaches
    # its scripts at all.
    "godot_resource",
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
    # Rust's ``field_declaration`` only. Every other language maps its fields
    # and properties to ``variable``; Rust keeps them separate, and the call
    # resolver relies on that to refuse a field as a bare-name call target
    # (``_NON_CALLABLE_KINDS``). Normalising this to ``variable`` would make
    # Rust fields indistinguishable from callable values and silently undo
    # that refusal, so it is declared here rather than mapped away.
    "property",
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

    @property
    def local_names(self) -> list[str]:
        """The names this statement writes into the importing file's namespace.

        ``imported_names`` carries names as they exist in the *source* module,
        because that is what reachability matches against — under an alias
        (``import { A as B }``, ``from m import a as b``) the two differ. A
        caller that wants to recognise an identifier *as it appears in this
        file* — a router DSL naming a handler, an exclusion list for call
        resolution — must read the bindings instead, and this is that read.

        Falls back to ``imported_names`` for the languages whose extractors
        emit no bindings, where the two are the same list by construction. For
        a re-export nothing is truly bound locally; the alias is returned,
        being the token that appears in this file.
        """
        return [b.local_name for b in self.bindings] or list(self.imported_names)


@dataclass
class CallReceiver:
    """A call expression used as the receiver of another call.

    Keeping this AST-derived fact separate from ``receiver_name`` prevents a
    chained call from being mistaken for an unqualified/free call.
    """

    target_name: str
    receiver_name: str | None
    argument_count: int | None


#: What a resolved call site becomes in the graph. A syntactic form that names
#: a symbol without invoking it still runs the call tiers -- deciding which
#: symbol a name means is the same problem -- but must not reach the graph as
#: ``calls``. Both members are ``EdgeType`` members; this is the subset a call
#: site may produce.
CallSiteEdgeType = Literal["calls", "references"]


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
    receiver_call: CallReceiver | None = None  # set only for a captured chained call
    # C++ `Ns::f()` / `Class::m()`: the qualifier as written. Kept apart from
    # ``receiver_name`` because that field routes to the member strategies,
    # and cpp has none -- a scoped call must stay on the free-call path.
    scope_name: str | None = None
    edge_type: CallSiteEdgeType = "calls"  # see ``CallSiteEdgeType``
    supplied_props: set[str] | None = None  # prop names supplied in JSX element (None if unknown/spread)


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
    # Base method → an implementation that can answer for it. Named for what
    # it asserts rather than for a heritage relation: the pass matches by
    # method name and compares no signature, so it is a possible dispatch
    # target, not a proven override. `method_implements` could not be reused —
    # it runs implementor → interface, and this edge has to point the other
    # way for a traversal starting at the base to reach the implementation.
    "dispatches_to",
    "co_changes",
    "framework",
    # The symbol-level sibling of `framework`, emitted when a handler can name
    # both ends as symbols. Deliberately not `calls`: nothing here is a call the
    # parser could have seen, and letting it read as one would put an inferred
    # wiring hop into an execution flow as if it were source.
    "framework_binds",
    # A data reference rather than a call: C# member access, file → file. It
    # used to name two unrelated things — the Express route wiring emitted it
    # symbol → symbol — which is why one consumer set could not say which layer
    # it meant. That producer now emits `framework_binds`.
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
    # A function named without being called: a dispatch-table entry, a
    # callback field, an argument to a registration macro (currently C/C++;
    # see the ``@reference.name`` captures). Symbol → symbol, and deliberately
    # not ``calls`` — nothing here proves the function is ever invoked, only
    # that something holds a handle to it, which is enough to make deleting it
    # unsafe.
    "references",
]

# Runtime mirror of the Literal, for the places that must test membership
# rather than annotate. Derived, never hand-written — a second hand-written
# copy is the exact failure this phase exists to remove.
EDGE_TYPE_VALUES: frozenset[str] = frozenset(get_args(EdgeType))

# Which resolution strategy produced a `calls` / `references` edge. Same
# closed-vocabulary discipline as `EdgeType`, held shut by the same test.
#
# An edge is not a fact. `global_unique` binds a name to the only symbol
# carrying it anywhere in the repo, which is a guess; `same_file` is a
# certainty. Both used to reach a consumer as an unlabelled arrow, so an agent
# reading a flow could not tell which it was looking at.
#
# Every origin has exactly one confidence (see `CallResolver`), so the origin
# distribution and the confidence histogram are two views of the same data —
# which is what makes the stamping checkable against the existing numbers.
# NULL on an edge means the row predates this vocabulary, not "unknown origin".
ResolutionOrigin = Literal[
    "same_file",  # 0.95 — defined in the calling file
    "self_scope",  # 0.95 — self/this, method on the caller's own class
    "enclosing_class",  # 0.95 — bare call, bound to the caller's own class
    "receiver_same_file",  # 0.93 — receiver names a class in this file
    "same_package",  # 0.90 — Go/JVM sibling file, no import needed
    "import_scoped",  # 0.90 — the name was imported from the defining file
    "receiver_same_package",  # 0.90 — receiver is a same-package class (JVM)
    "package_alias",  # 0.88 — Go pkg.Func, resolved across the whole package
    "module_alias",  # 0.88 — receiver is an imported module
    "crate_root",  # 0.88 — Rust crate-scoped reference
    "receiver_import",  # 0.88 — receiver class found in an imported file
    "import_merged",  # 0.85 — in *some* imported file; which one is unattributed
    # 0.93 — C/C++ `Qualifier::name()`. The class is written at the call
    # site and declares the method, so nothing is inferred; it ranks with
    # `receiver_same_file` because both rest on a class NAME matching, and
    # two namespaces may spell one class name.
    "scoped_name",
    "same_target",  # 0.85 — C/C++ sibling TU of the same build target
    # 0.75 — the (class, method) pair exists somewhere in the repo. The member
    # analogue of `global_unique`, and no more than that: the strategy is
    # named for Rust trait impls but is gated on no language.
    "receiver_global",
    "global_unique",  # 0.50 — the name is unique repo-wide. A guess.
    # The four below reach the same scopes as their untyped twins, but through
    # a receiver whose type was read off its declaration rather than written at
    # the call. They share their twin's confidence deliberately: the inferred
    # type had to declare the method before an edge was emitted, so the
    # evidence is no weaker — what differs is how the receiver was named, and
    # that is precisely what an origin is for.
    "receiver_typed_same_file",  # 0.93
    "receiver_typed_same_package",  # 0.90 (JVM)
    "receiver_typed_import",  # 0.88
    "receiver_typed_global",  # 0.75
    # The same four scopes again, for a receiver typed from the enclosing
    # class's fields rather than from the calling body. Kept apart from the
    # four above because they are a different scan over a different scope, and
    # an origin that cannot separate them cannot be audited.
    "receiver_field_same_file",  # 0.93
    "receiver_field_same_package",  # 0.90 (JVM)
    "receiver_field_import",  # 0.88
    "receiver_field_global",  # 0.75
    # The same four scopes once more, for a receiver a framework decorator
    # retyped — `@shared_task def add` is a `Task`, so `add.s()` is `Task::s`.
    # Separate because the evidence is a decorator table, not a declaration.
    "receiver_framework_same_file",  # 0.93
    "receiver_framework_same_package",  # 0.90
    "receiver_framework_import",  # 0.88
    "receiver_framework_global",  # 0.75
    # Chained receiver typed from the inner callee's declared return type.
    "return_type_same_file",  # 0.93
    "return_type_same_package",  # 0.90 (JVM)
    "return_type_import",  # 0.88
    "return_type_global",  # 0.75
    # 0.90 — the caller's own class does not declare the method but exactly one
    # of its ancestors does. Below the two same-class origins because the walk
    # compares no signature and reads no visibility, so it can reach a method
    # the language would not actually dispatch to.
    "self_inherited",  # 0.90 — explicit self/this receiver
    "enclosing_inherited",  # 0.90 — implicit receiver, bare call
]

RESOLUTION_ORIGIN_VALUES: frozenset[str] = frozenset(get_args(ResolutionOrigin))

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
# `reads` is a member here for a reason that no longer holds: its symbol-level
# producer moved to `framework_binds`, so `csharp_member_reads` is the only one
# left and it emits file → file. A file node can never be a symbol node's
# predecessor, so membership is inert rather than wrong. Retiring it moves the
# vocabulary and belongs to a diff that can measure that.
SYMBOL_USE_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "calls",
        "extends",
        "implements",
        "method_implements",
        # An implementation is used by every call written against the base it
        # answers for, and that call lands on the base. Without this the whole
        # implementation side of an interface reads as called by nobody.
        "dispatches_to",
        # A fixture nobody calls and a collaborator nobody constructs are both
        # used — by the container, which no parser sees.
        "framework_binds",
        "reads",
        # Naming a function is using it. A handler sitting in a dispatch table
        # is never called anywhere a parser can see, and treating that as "no
        # use" reported entire registration layers as safe to delete (#1602).
        "references",
    }
)


# Symbol → symbol edges along which control can actually reach the target, for
# the question "would running this test execute that code?". The reachability
# view minus the two that record a mention rather than a transfer of control:
# `references` is a name sitting in a dispatch table and `reads` is a field
# access, and neither runs the thing it names. Narrower than
# SYMBOL_USE_EDGE_TYPES on purpose — dead code asks "is this used", which a
# mention answers, and the inferred test map asks "is this run", which it does
# not.
EXECUTION_EDGE_TYPES: frozenset[str] = SYMBOL_USE_EDGE_TYPES - {"references", "reads"}


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
    # ``{exported name: local name}`` where a TS/JS module publishes a symbol
    # it declares under a different name (``export { stringType as string }``).
    # Empty for every other language and for the far commoner clause that does
    # not rename, where the symbol table already answers.
    export_aliases: dict[str, str] = field(default_factory=dict)
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
    # C/C++ sites that name a function without calling it: a dispatch table
    # entry, a callback field, an argument to a registration macro. Reuses
    # ``CallSite`` because resolving one is the identical problem — a name, the
    # symbol enclosing it, and a line — and it lets these ride the same
    # resolution tiers. They become ``references`` edges, not ``calls``.
    references: list[CallSite] = field(default_factory=list)


def symbol_id_language(parsed_files: dict[str, ParsedFile], symbol_id: str) -> str | None:
    """Language of the file a symbol ID belongs to, or ``None`` if unparsed.

    Lives beside the ID vocabulary rather than in either resolver — both need
    it for the same cross-language rejection.
    """
    file_path = symbol_id.split("::")[0] if "::" in symbol_id else symbol_id
    parsed = parsed_files.get(file_path)
    return parsed.file_info.language if parsed else None


def compute_content_hash(source: bytes) -> str:
    """Return the SHA-256 hex digest of *source*."""
    return hashlib.sha256(source).hexdigest()
