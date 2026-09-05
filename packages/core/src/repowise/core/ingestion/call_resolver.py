"""Three-tier call resolution engine for the symbol-level dependency graph.

Resolves CallSite objects (extracted from AST) to concrete symbol node IDs
in the graph, producing CALLS edges with confidence scores.

Resolution tiers (checked in order, first match wins):

    Tier 1 — Same-file exact match (confidence 0.95)
        The call target matches a symbol defined in the same file.

    Tier 2 — Import-scoped match (confidence 0.90)
        The call target matches a symbol in a file that the caller imports,
        optionally scoped by the specific imported names.

    Tier 3 — Global unique match (confidence 0.50)
        The call target matches exactly one symbol across the entire codebase.
        Only fires when the match is unambiguous to avoid false edges.

Each resolved call produces a (source_id, target_id, confidence, origin) tuple
that the GraphBuilder converts into a CALLS edge. The tiers above are the
headline three; ``ResolutionOrigin`` in ``models`` is the full set, one name
per strategy, and it is what the edge carries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import structlog

from .language_data import (
    get_builtin_methods,
    get_external_receiver_types,
    get_external_return_types,
)
from .languages.receiver_types import (
    FRAMEWORK_DECORATOR_LANGUAGES,
    IMPLICIT_FIELD_LANGUAGES,
    RECEIVER_TYPE_LANGUAGES,
    Declaration,
    framework_decorated_type,
    names_in_span,
    scan_bindings,
    scan_declarations,
    types_by_class,
    types_in_span,
    unwrapped_names_in_span,
)
from .models import (
    CallReceiver,
    CallSite,
    CallSiteEdgeType,
    NamedBinding,
    ParsedFile,
    ResolutionOrigin,
    symbol_id_language,
)
from .return_types import declared_return_type, normalize_return_type, signature_parameter_count
from .type_names import POINTER_LIKE_MEMBERS

log = structlog.get_logger(__name__)

# Languages with an implicit method receiver, where a bare ``foo()`` binds to
# the caller's instance. Go, Python, PHP and JS/TS spell the receiver out, so a
# bare call there is a free function and this tier would resolve it wrongly.
_IMPLICIT_RECEIVER_LANGUAGES = frozenset({"java", "csharp", "cpp", "kotlin"})

# Languages where a call the caller's own class cannot answer is looked for on
# its ancestors. Both shapes — an explicit ``self``/``this`` receiver and an
# implicit one — end in the same walk.
#
# C++ is absent because its heritage binds a qualified external
# parent to a same-named local type, which puts unrelated siblings in one
# hierarchy before this walk even runs. Java is absent because `java.scm`'s
# bare-call pattern also matches `this.field.m()`, which no receiver-carrying
# pattern claims, so such a call arrives here indistinguishable from a real
# implicit receiver — and its measured population on two Java repos was zero,
# so the tier could only cost.
#
# C# is present, and was once wrongly removed: a name's overloads all share one
# symbol id, so a declaration line read back out of the graph names an
# arbitrary overload. That reads as a wrong target and is not one — the id
# these calls resolve to is the id C# binds.
_INHERITED_LANGUAGES = frozenset({"kotlin", "python", "typescript", "swift", "csharp"})

# Languages where a bare name is scoped lexically: it can only mean the
# caller's own module, an explicit ``import``, or the prelude. Elixir's
# ``alias`` / ``require`` / ``use`` bind a module name, never a function name,
# so repo-wide uniqueness is no evidence and only wildcard imports may merge
# names. F# is the same rule with different spelling: a bare name means the
# enclosing scope, a module the file has ``open``ed, or FSharp.Core, and
# nothing else -- a name unique across the repo is not thereby in scope.
_LEXICAL_BARE_NAME_LANGUAGES = frozenset({"elixir", "fsharp"})

# The sentinel an import that binds a whole module's public names carries.
_WILDCARD_IMPORTED_NAMES = ["*"]

# Ancestors within four hops: ``heritage_ancestors`` bounds expansion, not
# reach, so 3 reaches 4.
_MAX_ANCESTOR_EXPAND_DEPTH = 3


@dataclass(frozen=True, slots=True)
class _LanguageCallStrategies:
    """What a language resolves that the language-neutral tiers cannot.

    ``free`` runs after the same-file tier and before the import tiers;
    ``member`` runs before every receiver strategy; ``member_fallback`` runs
    after all of them, so a strategy there only ever sees a call nothing else
    claimed and can only add an edge. All three stop at the first hit.
    Strategies are named rather than bound so a probe can substitute one.
    """

    free: tuple[str, ...] = ()
    member: tuple[str, ...] = ()
    member_fallback: tuple[str, ...] = ()


_NO_LANGUAGE_STRATEGIES = _LanguageCallStrategies()

_TYPED_RECEIVER = ("_resolve_typed_receiver",)

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})

# Kinds that can never be the callee of a call, used to keep the bare-name
# Tier 3 index from offering a data member as a function.
#
# This is deliberately NOT the complement of ``_FUNCTION_KINDS``. Measured over
# the corpus, plenty of non-function kinds are legitimately called: ``class``
# is a constructor in python/java/c#/typescript; ``variable`` is both a rust
# tuple ``enum_variant`` (309 grounded call edges on goose) and a typescript
# const whose initialiser is not syntactically a function, such as a factory
# result or a ``.bind()`` handle (2,173 on zod); ``type_alias`` is a Go
# conversion. Denying by function-ness would delete thousands of real edges.
#
# ``property`` is the one kind in the whole of ``language_configs.py`` that
# means "data member" and nothing else: it is emitted by exactly one mapping,
# rust's ``field_declaration``. Every other language spells its fields
# ``variable``, which is why this fix cannot be extended to them — there a
# field is indistinguishable from a callable value by kind alone.
_NON_CALLABLE_KINDS = frozenset({"property"})

# A getter and its setter are two declarations under one id, which reads as an
# overload set and is not one: the name is an attribute, not a callable.
_PROPERTY_DECORATORS = frozenset({"property", "cached_property"})
_PROPERTY_ACCESSOR_SUFFIXES = (".setter", ".getter", ".deleter")


def _is_property_accessor(sym: Any) -> bool:
    for decorator in getattr(sym, "decorators", ()) or ():
        tail = decorator.lstrip("@").strip()
        if tail in _PROPERTY_DECORATORS or tail.endswith(_PROPERTY_ACCESSOR_SUFFIXES):
            return True
    return False

_JVM_STRATEGIES = _LanguageCallStrategies(
    free=("_resolve_jvm_same_package",),
    member=("_resolve_jvm_receiver_same_package",),
)

# C++ reaches the typed fallback and registers no `member` strategy, so an
# `obj->m()` is looked for in the caller's own file, in what it includes, and
# then in the global pair index. `c` shares this object and is excluded a layer
# up instead: it is absent from `_LANGUAGE_PATTERNS`, and a struct declares no
# method for the pair index to hold.
_CPP_STRATEGIES = _LanguageCallStrategies(
    free=("_resolve_cpp_scoped_call", "_resolve_cpp_same_target"),
    member_fallback=_TYPED_RECEIVER,
)

# Rust's crate-root strategy is deliberately absent: it runs for every language
# today, and gating it here would drop crate-name receivers in mixed repos.
_LANGUAGE_CALL_STRATEGIES: dict[str, _LanguageCallStrategies] = {
    # Go's package tier runs first and claims ``pkg.Func()`` outright, so a
    # package qualifier never reaches the typed fallback — 41% of gitleaks'
    # lowercase-receiver misses are package names, and this is what keeps them
    # out of it.
    "go": _LanguageCallStrategies(
        free=("_resolve_go_same_package",),
        member=("_resolve_go_package_call",),
        member_fallback=_TYPED_RECEIVER,
    ),
    # Kotlin shares the JVM tiers and, since its declaration shapes landed,
    # the typed-receiver fallback too. One `name: Type` shape reaches its
    # typed vals, vars and parameters alike, so the language gate no longer
    # declines the moment the fallback asks.
    # Java takes the uniqueness-gated package tier, Kotlin the open one; see
    # ``_resolve_java_same_package_unique`` for why that is a language rule.
    "java": replace(
        _JVM_STRATEGIES,
        free=("_resolve_java_same_package_unique",),
        member_fallback=_TYPED_RECEIVER,
    ),
    "kotlin": replace(_JVM_STRATEGIES, member_fallback=_TYPED_RECEIVER),
    "csharp": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "python": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # Swift registers the fallback and nothing else: it has no package
    # tier of its own, so a typed receiver is looked for in the caller's
    # file, in what the file imports, and then in the global pair index.
    "swift": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "cpp": _CPP_STRATEGIES,
    "c": _CPP_STRATEGIES,
}

_SOURCE_CACHE_FILES = 4
_BODY_TYPE_CACHE_ENTRIES = 2048

# Phase admission is intentionally explicit. P16 lands the behavior-preserving
# substrate with no language enabled; later phases add only measured lanes.
PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES: frozenset[str] = frozenset({"cpp"})


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """A call resolved to concrete symbol IDs with a confidence score."""

    caller_id: str  # symbol node ID of the calling function/method
    callee_id: str  # symbol node ID of the called function/method
    confidence: float  # 0.0–1.0
    line: int  # call site line number (for diagnostics)
    origin: ResolutionOrigin  # which strategy below produced it
    edge_type: CallSiteEdgeType = "calls"  # carried through from the CallSite
    supplied_props: frozenset[str] | None = None  # prop names supplied in JSX element (None if unknown/spread)


def _same_translation_unit(decl_file: str, def_file: str) -> bool:
    """Are these two paths the same C++ translation unit?

    Compared on the base name, because a public header rarely sits beside its
    implementation (``include/pkg/thing.h`` against ``src/thing.cc``). The
    include relation would be the better test, but a C++ include binds to the
    path as written and usually is not a file key.
    """
    return (
        decl_file == def_file
        or PurePosixPath(decl_file).stem == PurePosixPath(def_file).stem
    )


class CallResolver:
    """Resolve raw CallSites to symbol-level edges.

    Constructed once per ``GraphBuilder.build()`` call with the full set of
    parsed files and import edges, then driven one file at a time.

    ``resolve_file()`` is **not** safe to call concurrently. Several lazy
    caches are filled during resolution, and the capped ones evict wholesale.
    Every cached value is a pure function of state fixed at construction, so a
    race would cost recomputation rather than a wrong answer, but the eviction
    makes concurrent use pointless as well as unsupported.
    """

    def __init__(
        self,
        parsed_files: dict[str, ParsedFile],
        import_targets: dict[str, set[str]],
        *,
        repo_path: str | None = None,
        import_maps: Any | None = None,
        heritage_parents: dict[str, set[str]] | None = None,
        return_type_chain_languages: frozenset[str] | None = None,
    ) -> None:
        # {type symbol id: parent type symbol ids}, from the caller's already
        # resolved heritage. Absent when the resolver is built standalone, in
        # which case the inherited tier simply never fires.
        self._heritage_parents: dict[str, set[str]] = heritage_parents or {}
        self._return_type_chain_languages = (
            PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
            if return_type_chain_languages is None
            else return_type_chain_languages
        )
        self._ancestors: dict[str, tuple[str, ...]] = {}
        # Per-file symbol index: {file_path: {symbol_name: symbol_id}}
        self._file_symbols: dict[str, dict[str, str]] = {}

        # Per-file method index: {file_path: {(class_name, method_name): symbol_id}}
        self._file_methods: dict[str, dict[tuple[str, str], str]] = {}

        # Global method index: {(class_name, method_name): [(file_path, symbol_id)]}
        # in file-insertion order — replaces the trait-dispatch scan over
        # every file's method dict with one short-list lookup.
        self._global_methods: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)

        # Global symbol index: {name: [symbol_ids]} — for Tier 3
        self._global_symbols: dict[str, list[str]] = defaultdict(list)
        self._symbols_by_id = {
            symbol.id: symbol for parsed in parsed_files.values() for symbol in parsed.symbols
        }
        self._symbol_paths_by_id = {
            symbol.id: path
            for path, parsed in parsed_files.items()
            for symbol in parsed.symbols
        }
        self._overload_return_types: dict[tuple[str, str | None, str, int | None], set[str]] = (
            defaultdict(set)
        )
        for path, parsed in parsed_files.items():
            for symbol in parsed.symbols:
                raw_return = declared_return_type(symbol.signature or "")
                normalized = (
                    normalize_return_type(raw_return, symbol.language) if raw_return else None
                )
                if normalized is not None:
                    key = (
                        path,
                        symbol.parent_name,
                        symbol.name,
                        signature_parameter_count(symbol.signature or ""),
                    )
                    self._overload_return_types[key].add(normalized)
        self._known_type_names = frozenset(
            symbol.name for symbol in self._symbols_by_id.values() if symbol.kind in _TYPE_KINDS
        )

        # Symbols in the index above that are data members, not callables
        # Held as an id set rather than a full id->kind map: it is the only
        # kind question asked of it and the set is small.
        self._non_callable_ids: set[str] = set()
        self._property_accessor_ids: set[str] = set()

        # C/C++ forward declaration → the definition it declares. Populated by
        # ``_build_indices``; applied to every resolved call so the edge lands
        # on the body rather than the header line that announced it.
        self._decl_to_def: dict[str, str] = {}

        # Import graph: {file_path: set of imported file paths}
        self._import_targets = import_targets

        # Shared import-name maps (built once per GraphBuilder.build() and
        # injected; standalone construction builds them locally).
        if import_maps is None:
            from .import_index import build_import_name_maps

            import_maps = build_import_name_maps(parsed_files)
        # Import name mapping: {file_path: {local_name: source_file}}
        self._import_names: dict[str, dict[str, str]] = import_maps.import_names
        # Full binding data: {file_path: {local_name: NamedBinding}}
        self._import_bindings: dict[str, dict[str, NamedBinding]] = import_maps.import_bindings
        # Module alias mapping: {file_path: {alias: source_file}}
        self._module_aliases: dict[str, dict[str, str]] = import_maps.module_aliases

        # Lazy per-file merged views of every imported file's symbol /
        # method tables — turns the Tier-2b "scan each imported file"
        # loops into single dict lookups. Built on first miss per file;
        # merge order is sorted(import paths) with first-wins so shadowed
        # names resolve deterministically (the old set-iteration order was
        # hash-randomized per process).
        self._merged_import_symbols: dict[str, dict[str, str]] = {}
        self._merged_import_methods: dict[str, dict[tuple[str, str], str]] = {}

        # Lazy per-file set of (line, target) that also carry a receiver.
        self._member_shaped: dict[str, set[tuple[int, str]]] = {}

        # Receiver typing reads source text, so both caches are capped rather
        # than per-repo: files resolve one at a time, so a few slots always
        # hit, and the cap is what keeps a whole repo's source out of memory.
        # Scanning is memoised per *function*, not per reference — one scan
        # answers every unresolved receiver in a body.
        self._source_text: dict[str, str] = {}
        self._declarations: dict[str, tuple[Declaration, ...]] = {}
        self._symbol_spans: dict[str, dict[str, tuple[int, int]]] = {}
        self._body_types: dict[tuple[str, str], dict[str, str | None]] = {}
        self._field_types: dict[str, dict[str, dict[str, str | None]]] = {}
        self._bindings: dict[str, tuple[tuple[int, str], ...]] = {}
        self._bound_names: dict[tuple[str, str], frozenset[str]] = {}
        # {file: {name: type}} — module-level defs a framework decorator retyped.
        self._framework_types: dict[str, dict[str, str]] = {}
        self._external_names: dict[str, frozenset[str]] = {}
        self._repo_rebound_names: dict[str, frozenset[str]] = {}
        self._method_name_set: frozenset[str] | None = None
        self._framework_name_set: frozenset[str] | None = None

        # Barrel re-export origins: {barrel_file: {name: origin_file}}
        self._barrel_origins: dict[str, dict[str, str]] = defaultdict(dict)

        # Keep reference for cross-language checks in Tier 3
        self._parsed_files = parsed_files

        # Rust cross-crate resolution
        self._repo_path = repo_path
        self._rust_crate_src: dict[str, str] | None = None  # lazy

        # Go package-scoped resolution (lazy GoPackageIndex). ``_go_index``
        # holds the built index; ``_go_index_built`` distinguishes "not yet
        # built" from "built but unavailable" (no repo_path / no go files).
        self._go_index: Any = None
        self._go_index_built = False

        # JVM same-package resolution (lazy JvmWorkspaceIndex)
        self._jvm_index: Any = None
        self._jvm_index_built = False

        # C/C++ same-target resolution (lazy CppWorkspaceIndex)
        self._cpp_index: Any = None
        self._cpp_index_built = False

        self._strategies_by_file: dict[str, _LanguageCallStrategies] = {}

        self._build_indices(parsed_files)
        self._follow_barrel_exports()

    def _follow_barrel_exports(self) -> None:
        """Detect barrel/re-export files and record origin mappings.

        A barrel file imports a name and re-exports it without defining it
        locally (e.g., ``__init__.py`` with ``from .calculator import Calculator``).
        When downstream code imports from the barrel, we follow chains to
        find the actual defining file.
        """
        # First pass: identify direct barrel origins
        for path, name_to_file in self._import_names.items():
            file_syms = self._file_symbols.get(path, {})
            for name, source_file in name_to_file.items():
                # An origin outside the repo names no file any reader of this
                # map can look up, and the wildcard pass below already refuses
                # one. Holding the invariant in one pass and not its twin is
                # what makes a later reader look safe when it is not.
                if name not in file_syms and not source_file.startswith("external:"):
                    self._barrel_origins[path][name] = source_file

        # Track wildcard re-exports, which forward every symbol of the imported
        # module under this file's namespace. Two shapes qualify: Rust
        # `pub use foo::*` (is_reexport) and Python/JS `from foo import *` (a
        # "*" imported name). The latter is how package ``__init__.py`` barrels
        # commonly re-export a subpackage — ``build_import_name_maps`` skips the
        # "*" name (it is not a binding), so without this pass the barrel chain
        # dead-ends one hop short of the real definition and a call through the
        # barrel resolves to nothing.
        wildcard_sources: dict[str, list[str]] = defaultdict(list)
        for path, parsed in self._parsed_files.items():
            file_syms = self._file_symbols.get(path, {})
            for imp in parsed.imports:
                is_wildcard = imp.is_reexport or "*" in imp.imported_names
                if not is_wildcard or not imp.resolved_file:
                    continue
                if imp.resolved_file.startswith("external:"):
                    continue
                # ``export * as ns from "x"`` forwards the module under ``ns``,
                # so x's names are reachable as ``ns.name`` and are NOT this
                # file's own exports. Flattening them makes a bare ``name``
                # resolve into a nested namespace it was never in.
                if any(b.local_name == "*" and b.exported_name for b in imp.bindings):
                    continue
                resolved = imp.resolved_file
                if resolved != path:
                    wildcard_sources[path].append(resolved)
                source_syms = self._file_symbols.get(resolved, {})
                source_parsed = self._parsed_files.get(resolved)
                published = (
                    (*source_syms, *source_parsed.export_aliases)
                    if source_parsed
                    else tuple(source_syms)
                )
                for sym_name in published:
                    if sym_name not in file_syms:
                        self._barrel_origins[path][sym_name] = resolved

        # The pass above forwards only what the source file DECLARES, so a
        # barrel over a barrel forwards nothing and the chain breaks at its
        # first link rather than its last. Forward what the source file
        # re-exports too, to a fixpoint. The multi-hop pass below cannot do
        # this job — it deepens entries that exist, and here none do.
        #
        # Sorted so that a name two of this file's barrels both forward lands on
        # the same origin whatever order the repository was walked in.
        for _ in range(4):
            changed = False
            for path, sources in sorted(wildcard_sources.items()):
                file_syms = self._file_symbols.get(path, {})
                origins = self._barrel_origins[path]
                for source in sorted(sources):
                    source_bindings = self._import_bindings.get(source, {})
                    for name, declaring in sorted(self._barrel_origins.get(source, {}).items()):
                        if name in file_syms or name in origins or declaring == path:
                            continue
                        # The map keys a name as the source file spells it and
                        # records only the declaring file, never the name the
                        # symbol has there. A hop that renames therefore hands
                        # on a key the declaring file may coincidentally
                        # declare as something unrelated, and the receiving
                        # file carries no binding to undo it with. Refuse those
                        # rather than forward a name that means something else
                        # at the far end; it costs reach, never correctness.
                        binding = source_bindings.get(name)
                        if binding is not None and (binding.exported_name or name) != name:
                            continue
                        origins[name] = declaring
                        changed = True
            if not changed:
                break

        # Multi-hop: follow chains up to 5 hops
        for _ in range(4):
            changed = False
            for _path, origins in list(self._barrel_origins.items()):
                for name, source in list(origins.items()):
                    deeper = self._barrel_origins.get(source, {}).get(name)
                    if deeper and deeper != source:
                        origins[name] = deeper
                        changed = True
            if not changed:
                break

    def _get_rust_crate_src(self) -> dict[str, str]:
        """Lazily build a mapping from normalised crate name to src/ dir."""
        if self._rust_crate_src is not None:
            return self._rust_crate_src
        self._rust_crate_src = {}
        if not self._repo_path:
            return self._rust_crate_src
        from .resolvers.rust_workspace import get_or_build_cargo_workspace_index

        class _Ctx:
            def __init__(self, rp, pf):
                self.repo_path = rp
                self.parsed_files = pf

        ctx = _Ctx(self._repo_path, self._parsed_files)
        ws = get_or_build_cargo_workspace_index(ctx)
        if ws:
            for crate in ws.crates:
                normalized = crate.name.replace("-", "_")
                self._rust_crate_src[normalized] = crate.src_dir
        return self._rust_crate_src

    def _get_go_index(self) -> Any:
        """Lazily build the GoPackageIndex (or None if unavailable).

        Mirrors ``_get_rust_crate_src``: the resolver runs without a
        ``ResolverContext``, so it constructs a minimal stand-in and rebuilds
        the package index. The build is one walk over the ``.go`` files; the
        result is cached for the lifetime of the resolver.
        """
        if self._go_index_built:
            return self._go_index
        self._go_index_built = True
        if not self._repo_path:
            return None
        from pathlib import Path

        from .resolvers.go_workspace import build_go_package_index

        class _Ctx:
            def __init__(self, rp: str, pf: dict[str, ParsedFile]) -> None:
                self.repo_path = Path(rp)
                self.path_set = set(pf.keys())
                self.sorted_paths = tuple(sorted(self.path_set))
                self.parsed_files = pf
                self.go_modules: tuple[tuple[str, str], ...] = ()
                self.go_module_path: str | None = None

        self._go_index = build_go_package_index(_Ctx(self._repo_path, self._parsed_files))
        return self._go_index

    def _strategies_for(self, file_path: str) -> _LanguageCallStrategies:
        """The extra strategies this file's language gets."""
        parsed = self._parsed_files.get(file_path)
        language = parsed.file_info.language if parsed else ""
        return _LANGUAGE_CALL_STRATEGIES.get(language, _NO_LANGUAGE_STRATEGIES)

    def _get_cpp_index(self) -> Any:
        """Lazily build a CppWorkspaceIndex via a minimal stand-in context."""
        if self._cpp_index_built:
            return self._cpp_index
        self._cpp_index_built = True
        if not self._repo_path:
            return None
        from pathlib import Path

        from .resolvers.cpp_workspace import build_cpp_workspace_index

        class _Ctx:
            def __init__(self, rp: str, pf: dict[str, ParsedFile]) -> None:
                self.repo_path = Path(rp)
                self.path_set = set(pf.keys())
                self.sorted_paths = tuple(sorted(self.path_set))
                self.parsed_files = pf
                self.stem_map: dict[str, list[str]] = {}

        self._cpp_index = build_cpp_workspace_index(_Ctx(self._repo_path, self._parsed_files))
        return self._cpp_index

    def _collapse_declarations(self, sym_ids: list[str]) -> set[str]:
        """Fold each declaration onto the definition it was paired with.

        Two ids naming one symbol must not read as an ambiguity.
        """
        return {self._decl_to_def.get(sym_id, sym_id) for sym_id in sym_ids}

    def _resolve_cpp_scoped_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``Qualifier::name()`` against the class the qualifier names.

        The qualifier is written at the call site, so this infers nothing: the
        repository either declares ``Qualifier::name`` or it does not. Before
        it existed only the leaf name survived extraction, and `DB::Open()`
        bound to a test class's `Open`.

        It declines rather than refusing when the pair is unknown, because a
        qualifier may equally name a NAMESPACE and C++ namespaces are recorded
        on no symbol -- so absence here is not evidence of anything.
        """
        scope = call.scope_name
        if not scope:
            return None
        candidates = self._global_methods.get((scope, call.target_name))
        if not candidates:
            return None
        # A class name is not repository-unique. Prefer a declaration this file
        # actually includes; failing that accept a repo-wide unique one, and
        # otherwise leave it, because the qualifier has not settled which.
        imported = self._import_targets.get(file_path, ())
        preferred = [
            sym_id for f, sym_id in candidates if f == file_path or f in imported
        ]
        # A header's declaration and the .cc's definition are ONE symbol, and a
        # translation unit routinely sees both, so count them after the pairing
        # redirect or every paired method reads as ambiguous.
        if len(self._collapse_declarations(preferred)) == 1:
            sym_id = preferred[0]
        elif len(self._collapse_declarations([c[1] for c in candidates])) == 1:
            sym_id = candidates[0][1]
        else:
            return None
        if sym_id == caller_id:
            return None
        return ResolvedCall(caller_id, sym_id, 0.93, call.line, "scoped_name")

    def _resolve_cpp_same_target(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve a bare call against the workspace target's source list.

        C++ files in the same CMake/Bazel target share a build unit — an
        unqualified ``Helper(...)`` may be defined in any sibling
        ``.cc``/``.cpp`` in the same target with no ``#include`` line.
        """
        index = self._get_cpp_index()
        if index is None:
            return None
        siblings = index.siblings_in_targets(file_path)
        for sibling in siblings:
            syms = self._file_symbols.get(sibling, {})
            sym_id = syms.get(call.target_name)
            if sym_id is not None and sym_id != caller_id:
                return ResolvedCall(caller_id, sym_id, 0.85, call.line, "same_target")
        return None

    def _get_jvm_index(self) -> Any:
        """Lazily build the JvmWorkspaceIndex (or None if unavailable)."""
        if self._jvm_index_built:
            return self._jvm_index
        self._jvm_index_built = True
        if not self._repo_path:
            return None
        from pathlib import Path

        from .resolvers.jvm_workspace import build_jvm_workspace_index

        class _Ctx:
            def __init__(self, rp: str, pf: dict[str, ParsedFile]) -> None:
                self.repo_path = Path(rp)
                self.path_set = set(pf.keys())
                self.sorted_paths = tuple(sorted(self.path_set))
                self.parsed_files = pf

        self._jvm_index = build_jvm_workspace_index(_Ctx(self._repo_path, self._parsed_files))
        return self._jvm_index

    def _resolve_jvm_same_package(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve a bare call to a symbol defined in a same-package sibling.

        JVM files in the same package share a namespace — an unqualified
        identifier ``Helper`` may be a class or method defined in any sibling
        file of the same package, with no import statement.
        """
        return self._jvm_same_package(file_path, call, caller_id, unique_only=False)

    def _resolve_java_same_package_unique(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Same tier as ``_resolve_jvm_same_package``, refusing on ambiguity.

        Java-only because Kotlin has package-scope top-level and extension
        functions, so a bare name there really is a package lookup; Java has
        none, so it is a static import, an inherited member, or a member call
        whose receiver the grammar dropped. Hand-read, the removals agree:
        20 of 20 wrong on caffeine, 16 of 20 right on exposed and ktor.

        Refusing is not deleting. The chain continues into the import tiers,
        which answer 14,307 of caffeine's 18,390 refused sites.
        """
        return self._jvm_same_package(file_path, call, caller_id, unique_only=True)

    def _jvm_same_package(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        *,
        unique_only: bool,
    ) -> ResolvedCall | None:
        index = self._get_jvm_index()
        if index is None:
            return None
        found: str | None = None
        for sibling in index.same_package_files(file_path):
            sym_id = self._file_symbols.get(sibling, {}).get(call.target_name)
            if sym_id is not None and sym_id != caller_id:
                if not unique_only:
                    return ResolvedCall(caller_id, sym_id, 0.90, call.line, "same_package")
                if found is not None:
                    # Two siblings declare it and nothing here can tell them
                    # apart; this used to answer with whichever the index
                    # walked first.
                    return None
                found = sym_id
        if found is None:
            return None
        return ResolvedCall(caller_id, found, 0.90, call.line, "same_package")

    def _resolve_jvm_receiver_same_package(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``Receiver.method()`` where the receiver is a package sibling.

        JVM files in the same package see each other's types with no import,
        so the receiver may name a class declared in any sibling file.
        """
        key = (call.receiver_name or "", call.target_name)
        if key not in self._global_methods:
            return None
        index = self._get_jvm_index()
        if index is None:
            return None
        for sibling in index.same_package_files(file_path):
            sym_id = self._file_methods.get(sibling, {}).get(key)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "receiver_same_package")
        return None

    def _resolve_go_package_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``pkg.Func()`` against *every* file in the package.

        The legacy module-alias strategy resolves only against the single
        representative file the import resolved to; a function defined in a
        sibling file of that package is missed. Look it up across the whole
        package directory via the GoPackageIndex.
        """
        index = self._get_go_index()
        if index is None:
            return None
        module_file = self._module_aliases.get(file_path, {}).get(call.receiver_name)
        if not module_file:
            return None
        pkg = index.package_for_file(module_file)
        if pkg is None:
            return None
        for sibling in pkg.files:
            syms = self._file_symbols.get(sibling, {})
            sym_id = syms.get(call.target_name)
            if sym_id is not None and sym_id != caller_id:
                return ResolvedCall(caller_id, sym_id, 0.88, call.line, "package_alias")
        return None

    def _resolve_go_same_package(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve a bare call to a function defined in a sibling file.

        Files in the same Go package share a namespace with no import
        statement, so a bare ``Helper()`` may be defined in any sibling
        file. Search the package directory (excluding the caller's own
        file, already covered by the same-file tier).
        """
        index = self._get_go_index()
        if index is None:
            return None
        pkg = index.package_for_file(file_path)
        if pkg is None:
            return None
        for sibling in pkg.files:
            if sibling == file_path:
                continue
            syms = self._file_symbols.get(sibling, {})
            sym_id = syms.get(call.target_name)
            if sym_id is not None and sym_id != caller_id:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "same_package")
        return None

    def _build_indices(self, parsed_files: dict[str, ParsedFile]) -> None:
        """Build symbol lookup indices from parsed file data.

        (Import-name maps are shared — see ``import_index.build_import_name_maps``.)
        """
        # (parent, name) → [(file, symbol_id)] over symbols that carry a body,
        # and the bodiless declarations waiting to be paired against them.
        # Both feed ``_link_declarations`` once every file has been indexed.
        definitions: dict[tuple[str | None, str], list[tuple[str, str]]] = defaultdict(list)
        declarations: list[tuple[str, str, tuple[str | None, str]]] = []

        for path, parsed in parsed_files.items():
            file_syms: dict[str, str] = {}
            file_methods: dict[tuple[str, str], str] = {}

            for sym in parsed.symbols:
                decl_key = (sym.parent_name, sym.name)
                if sym.is_declaration:
                    declarations.append((path, sym.id, decl_key))
                    # A declaration must never displace a definition already
                    # indexed under this name — a .cpp that forward-declares a
                    # helper above its own body holds both.
                    #
                    # A method declaration stays out: this index answers
                    # unqualified lookups from importing files, and no bare name
                    # can legally reach a method. The (class, method) index
                    # below still takes it.
                    if sym.parent_name is None:
                        file_syms.setdefault(sym.name, sym.id)
                else:
                    definitions[decl_key].append((path, sym.id))
                    # File-level symbol index (top-level symbols and methods)
                    file_syms[sym.name] = sym.id

                # Method index: (class_name, method_name) → symbol_id
                if sym.parent_name:
                    key = (sym.parent_name, sym.name)
                    file_methods[key] = sym.id
                    self._global_methods[key].append((path, sym.id))

                # Global indices
                if sym.kind in _NON_CALLABLE_KINDS:
                    self._non_callable_ids.add(sym.id)
                if _is_property_accessor(sym):
                    self._property_accessor_ids.add(sym.id)
                # Same rule as the per-file index above, for the global-unique
                # tier.
                if not (sym.is_declaration and sym.parent_name is not None):
                    self._global_symbols[sym.name].append(sym.id)

            self._file_symbols[path] = file_syms
            self._file_methods[path] = file_methods

        self._decl_to_def = self._link_declarations(declarations, definitions)

    def _link_declarations(
        self,
        declarations: list[tuple[str, str, tuple[str | None, str]]],
        definitions: dict[tuple[str | None, str], list[tuple[str, str]]],
    ) -> dict[str, str]:
        """Pair each C/C++ forward declaration with the definition it declares.

        A header declares ``double Area(double)`` and a .cpp defines it, so the
        two land as separate same-named symbols. Every tier below Tier 1 looks
        the name up in the *header's* symbol table — the header is what the
        caller includes — so the call edge attached to the declaration and left
        the definition with no inbound edge at all, which read as dead code
        (#1601). Resolving the pairing here lets ``resolve_file`` move the edge
        onto the definition, where it belongs.

        Pairing prefers a definition whose translation unit includes the
        declaring header, which is the one-definition rule C++ actually means
        and keeps same-named functions in sibling namespaces apart. Failing
        that, a repo-wide unique definition is unambiguous enough to use. An
        overload set spanning several files matches neither test, and stays
        unlinked rather than guessed at.

        For a METHOD that fallback additionally requires the same translation
        unit: the key is ``(class, method)``, so a repo-wide unique definition
        proves the method name unique and says nothing about the class, and two
        unrelated classes of one name would pair across. A free function has no
        class identity to get wrong and is unchanged.
        """
        redirects: dict[str, str] = {}
        for decl_file, decl_id, key in declarations:
            candidates = definitions.get(key, ())
            if not candidates:
                continue
            # Deduped by symbol id, not by row: an overload set defined in one
            # file is several definitions sharing one id, and counting rows
            # reads that as an ambiguity that does not exist.
            including = {
                sym_id
                for def_file, sym_id in candidates
                if decl_file in self._import_targets.get(def_file, ())
            }
            distinct = {sym_id for _def_file, sym_id in candidates}
            if len(including) == 1:
                redirects[decl_id] = next(iter(including))
            elif len(distinct) == 1:
                def_file = candidates[0][0]
                if key[0] is None or _same_translation_unit(decl_file, def_file):
                    redirects[decl_id] = next(iter(distinct))
        return redirects

    @property
    def declaration_definitions(self) -> dict[str, str]:
        """``{declaration symbol id: definition symbol id}`` for paired decls.

        Read by the graph builder, which stamps the pairing on the declaration
        node so the dead-code pass can tell a superseded declaration from an
        orphaned prototype whose definition no longer exists.
        """
        return self._decl_to_def

    def _redirect_to_definition(self, resolved: ResolvedCall) -> ResolvedCall:
        """Move a call edge off a forward declaration onto its definition.

        No-op for every language but C/C++, and for the tiers that already
        landed on a definition.

        The self-edge guard carries a recursive function whose prototype sits
        in a header: Tier 1 declines to link the call to the body it is
        already inside, Tier 2 then finds the header declaration, and the
        redirect would point the edge straight back at the caller.
        """
        target = self._decl_to_def.get(resolved.callee_id)
        if target is None or target == resolved.caller_id:
            return resolved
        return ResolvedCall(
            resolved.caller_id,
            target,
            resolved.confidence,
            resolved.line,
            resolved.origin,
        )

    def _published(self, file_path: str, name: str) -> str | None:
        """The symbol *file_path* publishes under *name*, or None.

        A module may declare a symbol under one name and export it under
        another — ``export { stringType as string }`` — and every lookup that
        arrives through a namespace, a barrel or an import asks for the
        published name while the symbol table holds the local one. The table
        answers first, so the alias can only ever add a hit.
        """
        symbols = self._file_symbols.get(file_path, {})
        found = symbols.get(name)
        if found is not None:
            return found
        parsed = self._parsed_files.get(file_path)
        local = parsed.export_aliases.get(name) if parsed else None
        return symbols.get(local) if local else None

    def _merged_symbols_for(self, file_path: str) -> dict[str, str]:
        """Merged ``{name → symbol_id}`` across every file *file_path* imports.

        Sorted-path merge order with first-wins gives deterministic
        precedence for names exported by multiple imports.
        """
        merged = self._merged_import_symbols.get(file_path)
        if merged is None:
            merged = {}
            for imported_file in sorted(self._bare_name_import_sources(file_path)):
                if imported_file.startswith("external:"):
                    continue
                for name, sym_id in self._file_symbols.get(imported_file, {}).items():
                    merged.setdefault(name, sym_id)
            self._merged_import_symbols[file_path] = merged
        return merged

    def _bare_name_import_sources(self, file_path: str) -> set[str]:
        """The imported files a bare name in *file_path* may be looked up in.

        Every language but the lexically-scoped ones can use its whole import
        set: a name reaching this tier arrived through some import, and which
        directive carried it is not knowable from the resolved file alone. For
        a language in ``_LEXICAL_BARE_NAME_LANGUAGES`` it is knowable and it
        matters, so only imports that bind a whole module's public names count.
        """
        targets = self._import_targets.get(file_path, set())
        if self._language_of(file_path) not in _LEXICAL_BARE_NAME_LANGUAGES:
            return targets
        parsed = self._parsed_files.get(file_path)
        if parsed is None:
            return targets
        return {
            imp.resolved_file
            for imp in parsed.imports
            if imp.resolved_file in targets
            and list(imp.imported_names) == _WILDCARD_IMPORTED_NAMES
        }

    def _merged_methods_for(self, file_path: str) -> dict[tuple[str, str], str]:
        """Merged ``{(class, method) → symbol_id}`` across imports (see above)."""
        merged = self._merged_import_methods.get(file_path)
        if merged is None:
            merged = {}
            for imported_file in sorted(self._import_targets.get(file_path, ())):
                if imported_file.startswith("external:"):
                    continue
                for key, sym_id in self._file_methods.get(imported_file, {}).items():
                    merged.setdefault(key, sym_id)
            self._merged_import_methods[file_path] = merged
        return merged

    def resolve_file(self, file_path: str, calls: list[CallSite]) -> list[ResolvedCall]:
        """Resolve all calls in a single file to symbol-level edges."""
        results: list[ResolvedCall] = []

        for call in calls:
            if not call.caller_symbol_id:
                # Module-level call — assign to synthetic __module__ symbol
                call = replace(call, caller_symbol_id=f"{file_path}::__module__")

            resolved = self._resolve_one(file_path, call)
            if resolved:
                resolved = self._redirect_to_definition(resolved)
                # Stamped once here rather than in each tier: what a site
                # produces is a property of the syntax, not of the strategy
                # that answered it. No tier sets it, so this compares against
                # the default rather than against a tier's opinion.
                if call.edge_type != "calls":
                    resolved = replace(resolved, edge_type=call.edge_type)
                results.append(resolved)

        return results

    def _resolve_one(self, file_path: str, call: CallSite) -> ResolvedCall | None:
        """Resolve a single CallSite through the three-tier fallback."""
        caller_id = call.caller_symbol_id
        assert caller_id is not None

        language = self._language_of(file_path) or ""
        receiver_call = call.receiver_call
        # A language with an `external_return_types` table reaches the tier for
        # that table alone; only the constant above admits the full lane.
        if receiver_call is not None and (
            language in self._return_type_chain_languages
            or get_external_return_types(language)
        ):
            handled, resolved = self._resolve_return_typed_chain(
                file_path, call, caller_id, language
            )
            if handled:
                return resolved

        # --- Method call with receiver: receiver.method() ---
        if call.receiver_name:
            return self._with_props(self._resolve_member_call(file_path, call, caller_id), call)

        # --- Free function call: function() ---
        return self._with_props(self._resolve_free_call(file_path, call, caller_id), call)

    def _with_props(self, res: ResolvedCall | None, call: CallSite) -> ResolvedCall | None:
        if res is not None and call.supplied_props is not None:
            return replace(res, supplied_props=call.supplied_props)
        return res

    def _resolve_return_typed_chain(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        language: str,
    ) -> tuple[bool, ResolvedCall | None]:
        """Resolve or reject a chained outer call using the inner return type.

        ``handled`` distinguishes a proven refusal from missing evidence. A
        known repository type that does not declare the outer method disproves
        the legacy bare-name fallback; an absent/external type leaves legacy
        behavior untouched.
        """

        inner = call.receiver_call
        assert inner is not None

        tabled = self._external_chain_return_type(file_path, inner, language)
        from_table = tabled is not None
        if tabled is not None:
            type_name = tabled
        elif language not in self._return_type_chain_languages:
            # Admitted by its table alone. Inferring the head's type from the
            # declared return type of a repository symbol is a separate and much
            # larger population, and it is unmeasured here.
            return False, None
        else:
            inferred = self._inferred_chain_return_type(
                file_path, call, inner, caller_id, language
            )
            if inferred is None:
                return False, None
            type_name = inferred

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if language == "java":
            # A simple type name is not repository-unique.  Java package and
            # import binding settle its identity; the global tier does not.
            #
            # When the name came from the table it is external *in this file*,
            # and java has no extension methods, so the repository cannot
            # declare that type's method either.  That makes the bare-name
            # answer disproved rather than merely unevidenced, which is the
            # difference between refusing the site and falling through to it.
            if found is None or found[1] == "global":
                return from_table, None
        elif language == "cpp":
            # P17 admits only the measured Seastar debt family.  Broader C++
            # return-name matching remains probe evidence, not production
            # behaviour.
            if type_name != "future" or call.target_name != "get":
                return False, None
            if found is None:
                return (type_name in self._known_type_names), None
        elif language in ("csharp", "typescript"):
            # These lanes require a file or import/re-export identity.  A
            # repository-global simple type name is not a language binding.
            if found is None or found[1] == "global":
                return False, None
        elif found is None:
            return (type_name in self._known_type_names), None

        assert found is not None
        sym_id, tier = found
        return True, self._return_typed_call(caller_id, sym_id, tier, call.line)

    def _external_chain_return_type(
        self,
        file_path: str,
        inner: CallReceiver,
        language: str,
    ) -> str | None:
        """The table's return type for ``Type.method(..)`` at the head of a chain.

        None when the head is not a table entry, and — the part the rust half of
        this phase bought — when this file rebinds the name to something the
        repository owns. Java imports resolve to repository files, so
        ``_import_names`` answers that directly, where rust needs its raw import
        text read against the workspace index.

        The bound value has to be read, not merely tested: an unresolved import
        is recorded as an ``external:`` marker, so a truthiness check exempts
        ``import com.google.common.collect.Maps`` and silently drops 36 of
        caffeine's 96 measured sites.

        The import list alone is not enough, because java's same-package types
        need no import. A repository declaring its own ``Duration`` anywhere is
        exempted outright rather than same-package-checked: the table records
        the *JDK's* return type, which is the wrong answer for a repository
        type whose factory returns something else, and refusing on it would
        drop a correct edge. Costs nothing measured - 0 of the 106 sites has a
        repo-declared receiver name, by construction of the population.
        """
        receiver = inner.receiver_name
        if not receiver:
            return None
        methods = get_external_return_types(language).get(receiver)
        if methods is None:
            return None
        if receiver in self._known_type_names:
            return None
        bound = self._import_names.get(file_path, {}).get(receiver)
        if bound and not bound.startswith("external:"):
            return None
        return methods.get(inner.target_name)

    def _inferred_chain_return_type(
        self,
        file_path: str,
        call: CallSite,
        inner: CallReceiver,
        caller_id: str,
        language: str,
    ) -> str | None:
        """The head's type read off the repository symbol the inner call resolves to."""
        inner_call = CallSite(
            target_name=inner.target_name,
            receiver_name=inner.receiver_name,
            caller_symbol_id=caller_id,
            line=call.line,
            argument_count=inner.argument_count,
        )
        resolved_inner = self._resolve_one(file_path, inner_call)
        if resolved_inner is None:
            return None

        symbol = self._symbols_by_id.get(resolved_inner.callee_id)
        if symbol is None:
            return None
        if symbol.kind in _TYPE_KINDS:
            return symbol.name

        raw_return = declared_return_type(symbol.signature or "")
        type_name = normalize_return_type(raw_return, language) if raw_return else None
        symbol_path = self._symbol_paths_by_id.get(resolved_inner.callee_id)
        if symbol_path is None:
            return None
        overload_key = (
            symbol_path,
            symbol.parent_name,
            symbol.name,
            inner.argument_count,
        )
        if len(self._overload_return_types.get(overload_key, ())) > 1:
            return None
        return type_name

    def _return_typed_call(self, caller_id: str, sym_id: str, tier: str, line: int) -> ResolvedCall:
        """Stamp an edge whose receiver is the inner callee's return type."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "return_type_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "return_type_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "return_type_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "return_type_global")

    def _member_shaped_sites(self, file_path: str) -> set[tuple[int, str]]:
        """``(line, target)`` pairs at which this file also records a receiver.

        Several grammars match ``obj.m()`` twice — once with a receiver, once
        against the bare-call pattern — so a member call also arrives as a
        receiver-less site.

        Keyed on the line because a ``CallSite`` carries no column, so
        ``foo(bar.foo())`` suppresses the tier for its own bare ``foo()``. That
        costs the fix on that site, never a wrong edge; a column would fix it.
        """
        sites = self._member_shaped.get(file_path)
        if sites is None:
            parsed = self._parsed_files.get(file_path)
            sites = {
                (c.line, c.target_name) for c in (parsed.calls if parsed else ()) if c.receiver_name
            }
            self._member_shaped[file_path] = sites
        return sites

    def _enclosing_class_method(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> str | None:
        """The caller's own class's method of this name, or None.

        ``_file_symbols`` is flat and last-wins, so a bare ``foo()`` inside
        class ``A`` bound to class ``B``'s ``foo`` when ``B`` came later in the
        file. ``_file_methods`` already carries the class.
        """
        parsed = self._parsed_files.get(file_path)
        if parsed is None or parsed.file_info.language not in _IMPLICIT_RECEIVER_LANGUAGES:
            return None
        caller_class = _extract_class_from_symbol_id(caller_id)
        if not caller_class:
            return None
        if (call.line, call.target_name) in self._member_shaped_sites(file_path):
            return None
        return self._file_methods.get(file_path, {}).get((caller_class, call.target_name))

    def _resolve_free_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve a free function call (no receiver)."""
        target_name = call.target_name
        # Every tier keys on the target name, so a name the repo declares
        # nowhere can only be matched under an import alias (2a below).
        declared = target_name in self._global_symbols

        # Tier 1: same-file
        file_syms = self._file_symbols.get(file_path, {})
        if target_name in file_syms:
            callee_id = file_syms[target_name]
            own = self._enclosing_class_method(file_path, call, caller_id)
            if own is not None and own != callee_id and _rivals_a_class_method(callee_id):
                if own == caller_id:
                    # Recursion the flat index handed to a stranger. No edge.
                    return None
                return ResolvedCall(caller_id, own, 0.95, call.line, "enclosing_class")
            if callee_id != caller_id:  # no self-recursion edges for now
                return ResolvedCall(caller_id, callee_id, 0.95, call.line, "same_file")

        # The caller's language may see names no import statement mentions —
        # a Go or JVM package sibling, a C/C++ translation unit in the same
        # build target — and those beat the weaker import/global tiers.
        if declared:
            for strategy in self._strategies_for(file_path).free:
                hit = getattr(self, strategy)(file_path, call, caller_id)
                if hit is not None:
                    return hit

        # Tier 2: import-scoped
        # 2a: Check specific imported name → source file (binding-aware)
        binding = self._import_bindings.get(file_path, {}).get(target_name)
        if binding and binding.source_file:
            source_file = binding.source_file
            # Follow barrel re-export one hop
            barrel = self._barrel_origins.get(source_file, {})
            lookup_name = binding.exported_name or target_name
            if lookup_name in barrel:
                source_file = barrel[lookup_name]
            published = self._published(source_file, lookup_name)
            if published is not None:
                return ResolvedCall(caller_id, published, 0.90, call.line, "import_scoped")

        if not declared:
            return None

        # 2a fallback: plain _import_names (for imports without binding data)
        name_to_file = self._import_names.get(file_path, {})
        if target_name in name_to_file and not binding:
            source_file = name_to_file[target_name]
            barrel = self._barrel_origins.get(source_file, {})
            if target_name in barrel:
                source_file = barrel[target_name]
            published = self._published(source_file, target_name)
            if published is not None:
                return ResolvedCall(caller_id, published, 0.90, call.line, "import_scoped")

        # 2b: Check all imported files for the symbol (pre-merged lookup)
        merged_syms = self._merged_symbols_for(file_path)
        # A data member is not callable. Tier 3 already refuses one, but this
        # rung answered first and at 0.85, above the tier that declines it, so
        # the refusal only reached whichever sites tier 3 happened to see.
        if (
            target_name in merged_syms
            and merged_syms[target_name] not in self._non_callable_ids
        ):
            return ResolvedCall(
                caller_id, merged_syms[target_name], 0.85, call.line, "import_merged"
            )

        # Tier 3: global unique match — only within the same language.
        # A data member is not callable, so it must not be the unique answer
        # that mints an edge. Filtered here rather than at index build
        # so the `declared` gate above and the member gate in
        # ``_resolve_member_call`` keep seeing the whole repo.
        # Uniqueness is judged on the unfiltered list on purpose. Filtering the
        # pool *before* the length test would re-uniquify a name that a field
        # and a method both declare, firing the tier where it used to refuse —
        # measured at +916 new 0.50-confidence edges on goose, on the one tier
        # hand-read at 28.6% precision.
        #
        # A std-library name is refused the same way, and for the same reason
        # one rung up: the name is in scope in every file without an import,
        # so the repo symbol that happens to share it is not what the call
        # site named. `Ok(())` and a chained `.unwrap()` are the shape.
        candidates = self._global_symbols.get(target_name, [])
        if len(candidates) == 1 and candidates[0] != caller_id:
            return self._global_unique_match(
                file_path, call, caller_id, target_name, candidates[0]
            )

        # Last, so it can only add an edge. The member-shaped refusal is the
        # one ``_enclosing_class_method`` already applies: several grammars
        # mint a receiver-less site for ``obj.m()`` too, and reading one as an
        # implicit receiver would bind the wrong class's hierarchy to the call.
        lang = self._language_of(file_path)
        if (
            lang in _IMPLICIT_RECEIVER_LANGUAGES
            and lang in _INHERITED_LANGUAGES
            and (call.line, target_name) not in self._member_shaped_sites(file_path)
        ):
            sym_id = self._inherited_method(caller_id, target_name)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "enclosing_inherited")

        # An overload set is several declarations under one id, which the row
        # count reads as an ambiguity that is not there. Not the filtering
        # refused above: a field and a method sharing a name stay two ids.
        # Last on purpose - ahead of the tier above it restated 1,027 edges
        # the caller's own hierarchy already answered, at half the confidence.
        collapsed = self._collapse_declarations(candidates)
        if len(candidates) > 1 and len(collapsed) == 1:
            only = next(iter(collapsed))
            if only != caller_id and only not in self._property_accessor_ids:
                return self._global_unique_match(
                    file_path, call, caller_id, target_name, only
                )

        return None

    def _global_unique_match(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        target_name: str,
        candidate: str,
    ) -> ResolvedCall | None:
        """Tier 3's gates, applied to the one symbol the name resolves to."""
        language = self._language_of(file_path) or ""
        if language in _LEXICAL_BARE_NAME_LANGUAGES:
            # Repo-wide uniqueness says nothing about a lexically scoped name.
            return None
        if target_name in get_builtin_methods(language):
            return None
        if candidate in self._non_callable_ids:
            # Refused here rather than by falling through, so "this tier can
            # lose an edge but never gain one" is true of the control flow and
            # not only of the corpus.
            return None
        caller_lang = symbol_id_language(self._parsed_files, caller_id)
        callee_lang = symbol_id_language(self._parsed_files, candidate)
        if caller_lang and callee_lang and caller_lang != callee_lang:
            return None  # reject cross-language Tier 3 match
        return ResolvedCall(caller_id, candidate, 0.50, call.line, "global_unique")

    def _resolve_member_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve receiver.method() calls."""
        receiver_name = call.receiver_name
        method_name = call.target_name
        assert receiver_name is not None

        # Every strategy below ends in a lookup keyed on the method name, so a
        # name the repo declares nowhere cannot resolve. That is most member
        # calls — the callee is usually external — and this is the whole of
        # what those call sites now cost.
        if method_name not in self._global_symbols:
            return None

        # The caller's own file first. Every other tier is ordered narrow-first
        # and this one was not: the language strategies below run before
        # ``_receiver_pair_match``, so ``_resolve_jvm_receiver_same_package``
        # claimed ``new Builder<>(...).build()`` for any same-package class of
        # that name — a test-source-set one included — while the caller's own
        # file declared a private inner ``Builder`` on the same page. The
        # narrowest scope that can answer is the one the call actually means.
        own_file = self._file_methods.get(file_path, {}).get((receiver_name, method_name))
        if own_file is not None and own_file != caller_id:
            return ResolvedCall(caller_id, own_file, 0.93, call.line, "receiver_same_file")

        # A language may reach a receiver no import statement mentions: a Go
        # package alias spanning several files, a JVM class in the same package.
        for strategy in self._strategies_for(file_path).member:
            hit = getattr(self, strategy)(file_path, call, caller_id)
            if hit is not None:
                return hit

        # Strategy 1: receiver is a module alias (e.g. "import models" → "models.User()")
        module_file = self._module_aliases.get(file_path, {}).get(receiver_name)
        if module_file:
            published = self._published(module_file, method_name)
            if published is not None:
                return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")
            # A namespace over a barrel names a file that declares nothing of
            # its own, so the lookup above can only ever miss. Chase the
            # re-export map, as free calls and typed receivers already do.
            #
            # Keyed on the name the declaring file uses, not the one written
            # here: a member access cannot rename, but the re-export it arrives
            # through can, and the map records only the file. Without this an
            # ``export { foo as bar }`` binds any unrelated ``bar`` the
            # declaring file happens to hold.
            origin = self._barrel_origins.get(module_file, {}).get(method_name)
            if origin is not None and origin != module_file:
                binding = self._import_bindings.get(module_file, {}).get(method_name)
                declared_name = (binding.exported_name if binding else None) or method_name
                published = self._published(origin, declared_name)
                if published is not None:
                    return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")

        # Strategy 1b: receiver in import names (non-alias fallback for backward compat)
        name_to_file = self._import_names.get(file_path, {})
        if receiver_name in name_to_file and not module_file:
            source_file = name_to_file[receiver_name]
            published = self._published(source_file, method_name)
            if published is not None:
                return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")

        # Strategy 1c: Rust crate-scoped reference (e.g. typst_html::module)
        # The receiver is a crate name, the target is a symbol in that crate's lib.rs
        crate_src = self._get_rust_crate_src().get(receiver_name)
        if crate_src:
            for root_file in ("lib.rs", "main.rs"):
                crate_root = f"{crate_src}/{root_file}"
                root_syms = self._file_symbols.get(crate_root, {})
                if method_name in root_syms:
                    return ResolvedCall(
                        caller_id, root_syms[method_name], 0.88, call.line, "crate_root"
                    )

        # Strategies 2 and 2b: the receiver names a class that declares the
        # method — in this file, in an imported one, or anywhere at all.
        match = self._receiver_pair_match(file_path, (receiver_name, method_name))
        if match is not None:
            sym_id, tier = match
            if tier == "same_file":
                return ResolvedCall(caller_id, sym_id, 0.93, call.line, "receiver_same_file")
            if tier == "import":
                return ResolvedCall(caller_id, sym_id, 0.88, call.line, "receiver_import")
            if self._answers_for_a_foreign_type(file_path, receiver_name):
                return None
            return ResolvedCall(caller_id, sym_id, 0.75, call.line, "receiver_global")

        # Strategy 3: receiver is "self" or "this" — look in same class.
        # Only the caller's own file can hold the match, so index straight
        # into it instead of scanning every file's method dict.
        if receiver_name in ("self", "this"):
            caller_class = _extract_class_from_symbol_id(caller_id)
            if caller_class:
                sym_id = self._file_methods.get(file_path, {}).get((caller_class, method_name))
                if sym_id is not None and sym_id != caller_id:
                    return ResolvedCall(caller_id, sym_id, 0.95, call.line, "self_scope")

        # Last: the receiver may be a local or parameter, which names no class
        # at all. Everything above has already declined it.
        for strategy in self._strategies_for(file_path).member_fallback:
            hit = getattr(self, strategy)(file_path, call, caller_id)
            if hit is not None:
                return hit

        # Strategy 3, continued: the method may be inherited, and Strategy 3
        # can only see the caller's own class in the caller's own file. Asked
        # last so it can add an edge and never displace one.
        if receiver_name in ("self", "this") and self._language_of(file_path) in (
            _INHERITED_LANGUAGES
        ):
            sym_id = self._inherited_method(caller_id, method_name)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "self_inherited")

        return None

    def _language_of(self, file_path: str) -> str | None:
        parsed = self._parsed_files.get(file_path)
        return parsed.file_info.language if parsed else None

    def _inherited_method(self, caller_id: str, method_name: str) -> str | None:
        """The method of this name an ancestor of the caller's class declares.

        Ambiguity is terminal: when two ancestors on different branches declare
        the name there is no way to tell which one the call means, and picking
        either mints an edge to a class the call may never reach. Refusing
        costs an edge; guessing costs correctness.
        """
        if not self._heritage_parents:
            return None
        class_id = _extract_class_id(caller_id)
        if class_id is None:
            return None
        # The caller's own class answers, even when the earlier tier declined
        # it. Recursion is the case: Strategy 3 refuses to point a call at its
        # own symbol, and without this that refusal fell through to an
        # ancestor's bodiless declaration of the same name.
        if self._declares(class_id, method_name) is not None:
            return None
        hits = set()
        for ancestor in self._ancestors_of(class_id):
            sym_id = self._declares(ancestor, method_name)
            if sym_id is not None and sym_id != caller_id:
                hits.add(sym_id)
        return next(iter(hits)) if len(hits) == 1 else None

    def _declares(self, class_id: str, method_name: str) -> str | None:
        """The symbol a type node declares under *method_name*, or None.

        Splitting on the *first* separator, not the last: a nested class is
        ``path::Outer::Inner`` and ``_file_methods`` keys it under ``Inner``
        in ``path``. Taking the last would look for a file called
        ``path::Outer``, miss silently, and — worse than a missed edge — hide
        an ancestor from the ambiguity check above, letting a wrong single
        candidate through as if it were unopposed.
        """
        file_path, _, name = class_id.partition("::")
        return self._file_methods.get(file_path, {}).get((name.rpartition("::")[2], method_name))

    def _ancestors_of(self, class_id: str) -> tuple[str, ...]:
        got = self._ancestors.get(class_id)
        if got is None:
            from .heritage_resolver import heritage_ancestors

            # Sorted, because the walk stops expanding an anchor after its
            # first visit: which branch reaches it first decides how much of
            # its own chain is expanded, and a set's order is not stable
            # across processes.
            reached = heritage_ancestors(
                class_id,
                lambda t: sorted(self._heritage_parents.get(t, ())),
                max_expand_depth=_MAX_ANCESTOR_EXPAND_DEPTH,
            )
            reached.discard(class_id)
            got = tuple(sorted(reached))
            self._ancestors[class_id] = got
        return got

    def _receiver_pair_match(
        self,
        file_path: str,
        key: tuple[str, str],
    ) -> tuple[str, str] | None:
        """The symbol a ``(class, method)`` pair names, and the scope that held it.

        No class declares the pair unless the global method index holds it, and
        that check also keeps the merged-import view from being built for a
        pair that cannot be in it.
        """
        if key not in self._global_methods:
            return None

        file_methods = self._file_methods.get(file_path, {})
        if key in file_methods:
            return file_methods[key], "same_file"

        merged_methods = self._merged_methods_for(file_path)
        if key in merged_methods:
            return merged_methods[key], "import"

        # Trait method dispatch — the method may be defined on a trait's impl
        # block in another file. The global index preserves file-insertion
        # match order; the caller's own file is skipped.
        for path, sym_id in self._global_methods[key]:
            if path != file_path:
                return sym_id, "global"

        return None

    def _typed_receiver_language(self, file_path: str, call: CallSite) -> str | None:
        """The language receiver typing may run in here, or None to decline.

        Shared by both typed strategies so the cheap refusals happen once and
        in the same order: a receiver that names no local, a language with no
        declaration shapes, and a method name no class in the repo declares.
        """
        receiver_name = call.receiver_name or ""
        # A capitalised receiver already names a type and every tier above has
        # tried it. An underscore one cannot be anything but a name.
        head = receiver_name[:1]
        if not (head.islower() or head == "_") or receiver_name in ("self", "this"):
            return None

        parsed = self._parsed_files.get(file_path)
        language = parsed.file_info.language if parsed else ""
        if language not in RECEIVER_TYPE_LANGUAGES:
            return None

        # Reading a file is the expensive half, so refuse before it rather than
        # after. Nothing here can resolve unless some class declares a method of
        # this name; the gate above only proves some *symbol* does, which a free
        # function satisfies.
        if call.target_name not in self._method_names():
            return None
        return language

    def _typed_receiver_target(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
    ) -> tuple[str, str] | None:
        """The symbol ``type_name.method()`` names, and the scope that held it.

        The scope is returned rather than an edge so that each caller stamps
        its own origin literal, which is what keeps a field-typed edge separable
        from a body-typed one after the build.
        """
        key = (type_name, call.target_name)

        # An import statement binds the name outright, so it settles which type
        # this is before any scope search.
        bound = self._import_names.get(file_path, {}).get(type_name)
        if bound is not None and not bound.startswith("external:"):
            sym_id = self._file_methods.get(bound, {}).get(key)
            if sym_id is None:
                # An import names the module it was written against, which in
                # Python is usually a package ``__init__`` that re-exports the
                # type rather than declaring it. Free calls already chase that
                # chain; without it the import settles which type this is and
                # then refuses every method of it.
                #
                # Keyed on the *exported* name, as the free-call chase is: the
                # map holds each file's own local names, so an alias would ask
                # the bound file about a name that means something else there.
                # ``!= bound`` because a mutual re-export can leave an entry
                # naming its own file.
                binding = self._import_bindings.get(file_path, {}).get(type_name)
                exported = (binding.exported_name if binding else None) or type_name
                declaring = self._barrel_origins.get(bound, {}).get(exported)
                if declaring is not None and declaring != bound:
                    sym_id = self._file_methods.get(declaring, {}).get((exported, call.target_name))
            return None if sym_id is None else (sym_id, "import")

        # Bound to something outside the repo and there is no edge to find,
        # however many local classes share the simple name. A compatibility
        # test that imports a third-party `Cache` is otherwise read as calling
        # ours, and it looks right in every sample that does not check imports.
        if type_name in self._externally_bound_names(file_path):
            return None

        # The caller's own file first. A nested class here outranks a
        # same-named one in a package sibling, which is the whole ambiguity in
        # a repo that keeps near-duplicate implementations side by side.
        sym_id = self._file_methods.get(file_path, {}).get(key)
        if sym_id is not None:
            return sym_id, "same_file"

        # Only the JVM registers both a member strategy and these fallbacks, so
        # the scope that answers here is its same-package one. A second
        # language pairing the two needs an origin word of its own.
        typed_call = replace(call, receiver_name=type_name)
        for strategy in self._strategies_for(file_path).member:
            hit = getattr(self, strategy)(file_path, typed_call, caller_id)
            if hit is not None:
                return hit.callee_id, "same_package"

        return self._receiver_pair_match(file_path, key)

    def _resolve_typed_receiver(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``x.method()`` by typing ``x`` from its declaration.

        The declaration is in the calling body when ``x`` is a local or a
        parameter, and at the enclosing class's own scope when it is a field —
        the dependency-injection shape, held on a field and called throughout
        the class.

        Emits nothing unless the inferred type declares the method, which is
        what makes a text scan safe: a mis-inference reaches no index and
        yields no edge.
        """
        language = self._typed_receiver_language(file_path, call)
        if language is None:
            return None

        receiver_name = call.receiver_name or ""
        # A local shadows a field, so the body answers first and its answer
        # stands — including when that answer is "declared twice, no usable
        # type". Only a name the body never mentions reaches class scope.
        body_types = self._declared_types_in(file_path, caller_id, language)
        type_name = body_types.get(receiver_name)
        unbound = receiver_name not in body_types
        from_field = unbound and language in IMPLICIT_FIELD_LANGUAGES
        if from_field:
            class_id = caller_id.rpartition("::")[0]
            type_name = (
                self._field_types_in(file_path, language).get(class_id, {}).get(receiver_name)
            )
        # Third scope: a module-level def a framework decorator turned into an
        # instance. Neither of the two above can see it — it is not in the body
        # and not a field.
        from_framework = type_name is None and unbound and language in FRAMEWORK_DECORATOR_LANGUAGES
        if from_framework:
            # The type lookup is a dict hit and the shadowing scan reads the
            # whole file, so the cheap half decides first: only a receiver this
            # scope would actually answer for is worth scanning a body for.
            type_name = self._framework_type_of(file_path, receiver_name, language)
            if type_name is not None and receiver_name in self._bound_names_in(
                file_path, caller_id, language
            ):
                return None
        if type_name is None:
            return None
        if self._means_the_wrapper(file_path, caller_id, language, call, receiver_name):
            return None

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if found is None:
            return None
        sym_id, tier = found
        if from_framework:
            return self._framework_typed_call(caller_id, sym_id, tier, call.line)
        if from_field:
            return self._field_typed_call(caller_id, sym_id, tier, call.line)
        return self._body_typed_call(caller_id, sym_id, tier, call.line)

    def _means_the_wrapper(
        self,
        file_path: str,
        caller_id: str,
        language: str,
        call: CallSite,
        receiver_name: str,
    ) -> bool:
        """Is this call on the smart pointer itself rather than on what it holds?

        ``shared_ptr<Foo> p`` gives ``p->m()`` a ``Foo`` and ``p.m()`` a
        ``shared_ptr``, and the grammar query captures no operator to tell them
        apart. The names a dot call can reach are closed by the language, so
        refusing exactly those is what keeps ``p.get()`` off a repo's own
        ``Foo::get`` -- at the cost of an arrow call that really did mean one.
        Asked only of C++, and only of a type that was unwrapped.
        """
        if language != "cpp" or call.target_name not in POINTER_LIKE_MEMBERS:
            return False
        span = self._spans_for(file_path).get(caller_id)
        if span is None:
            return False
        return receiver_name in unwrapped_names_in_span(
            self._declarations_for(file_path, language), *span
        )

    def _body_typed_call(self, caller_id: str, sym_id: str, tier: str, line: int) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the calling body."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_typed_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_typed_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_typed_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_typed_global")

    def _field_typed_call(self, caller_id: str, sym_id: str, tier: str, line: int) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the enclosing class."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_field_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_field_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_field_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_field_global")

    def _framework_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge whose receiver was typed by a framework decorator."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_framework_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_framework_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_framework_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_framework_global")

    def _method_names(self) -> frozenset[str]:
        """Every name declared as a method of some class, built once."""
        if self._method_name_set is None:
            self._method_name_set = frozenset(method for _, method in self._global_methods)
        return self._method_name_set

    def _externally_bound_names(self, file_path: str) -> frozenset[str]:
        """Simple names this file imports from outside the repo.

        Read off the raw import statements rather than ``_import_names``, which
        only carries bindings that resolved to a file — precisely the ones this
        needs to exclude.

        The names wanted here are the ones *this file writes*, which is what
        ``Import.local_names`` answers: ``imported_names`` carries the source
        module's name, and under an alias the two differ.
        """
        names = self._external_names.get(file_path)
        if names is not None:
            return names

        parsed = self._parsed_files.get(file_path)
        found: set[str] = set()
        for imp in parsed.imports if parsed else ():
            if imp.resolved_file and not imp.resolved_file.startswith("external:"):
                continue
            bound = (*imp.local_names, imp.module_path.rsplit(".", 1)[-1])
            found.update(name for name in bound if name and name != "*")

        names = frozenset(found)
        if len(self._external_names) >= _SOURCE_CACHE_FILES:
            self._external_names.clear()
        self._external_names[file_path] = names
        return names

    def _answers_for_a_foreign_type(self, file_path: str, receiver_name: str) -> bool:
        """Is the repo-wide tier about to answer a call on a type we do not own?

        Asked only of the ``global`` tier, which takes the first file-order
        match for a ``(type, method)`` pair with no uniqueness check. A
        repository that writes ``impl RelationshipSourceCollection for
        Vec<Entity>`` declares a ``Vec::new``, and without this the tier hands
        it to every ``Vec::new()`` in the tree whatever the element type is.

        The narrower tiers above are deliberately left alone: both are grounded
        in the caller's own file or its imports, and a same-file ``impl
        From<LocalIndex> for usize`` really is what ``usize::from(i)`` means
        there.
        """
        if receiver_name not in get_external_receiver_types(
            self._language_of(file_path) or ""
        ):
            return False
        return receiver_name not in self._names_rebound_from_a_repo_package(file_path)

    def _names_rebound_from_a_repo_package(self, file_path: str) -> frozenset[str]:
        """Names this file imports from one of the repository's own packages.

        A file writing ``use bevy_platform::collections::HashMap`` means its own
        ``HashMap``, so the repo answer is right and the refusal above must not
        fire. The import list is what separates that from
        ``use std::collections::HashMap`` two files away; the name cannot.

        Read off the raw import statements because a rust import resolves to no
        repository file at all - measured 0 of 843 candidate rows - so
        ``_import_names`` cannot answer this. The package index is what does,
        and the exemption is only ever as good as the one the language has: a
        language given a non-empty ``external_receiver_types`` without a
        workspace index would refuse where it should exempt.
        """
        cached = self._repo_rebound_names.get(file_path)
        if cached is not None:
            return cached

        packages = self._get_rust_crate_src()  # keys are already `-`-normalised
        found: set[str] = set()
        parsed = self._parsed_files.get(file_path)
        for imp in parsed.imports if parsed and packages else ():
            head = imp.module_path.split("::")[0].replace("-", "_")
            if head in packages:
                found.update(n for n in (imp.local_names or ()) if n)

        names = frozenset(found)
        if len(self._repo_rebound_names) >= _SOURCE_CACHE_FILES:
            self._repo_rebound_names.clear()
        self._repo_rebound_names[file_path] = names
        return names

    def _declared_types_in(
        self,
        file_path: str,
        caller_id: str,
        language: str,
    ) -> dict[str, str | None]:
        """``{name: type}`` for the body of one function."""
        key = (file_path, caller_id)
        types = self._body_types.get(key)
        if types is not None:
            return types

        span = self._spans_for(file_path).get(caller_id)
        if span is None:
            types = {}
        else:
            types = types_in_span(self._declarations_for(file_path, language), *span)

        if len(self._body_types) >= _BODY_TYPE_CACHE_ENTRIES:
            self._body_types.clear()
        self._body_types[key] = types
        return types

    def _field_types_in(
        self,
        file_path: str,
        language: str,
    ) -> dict[str, dict[str, str | None]]:
        """``{class_id: {name: type}}`` for the fields one file's classes declare."""
        by_class = self._field_types.get(file_path)
        if by_class is not None:
            return by_class

        parsed = self._parsed_files.get(file_path)
        symbols = parsed.symbols if parsed else ()
        class_spans = {s.id: (s.start_line, s.end_line) for s in symbols if s.kind in _TYPE_KINDS}
        by_class = types_by_class(
            self._declarations_for(file_path, language),
            class_spans,
            [(s.start_line, s.end_line) for s in symbols if s.kind in _FUNCTION_KINDS],
        )
        if len(self._field_types) >= _SOURCE_CACHE_FILES:
            self._field_types.clear()
        self._field_types[file_path] = by_class
        return by_class

    def _bound_names_in(self, file_path: str, caller_id: str, language: str) -> frozenset[str]:
        """Every name the calling body binds, however it was bound."""
        key = (file_path, caller_id)
        names = self._bound_names.get(key)
        if names is None:
            span = self._spans_for(file_path).get(caller_id)
            if span is None:
                names = frozenset()
            else:
                names = names_in_span(self._bindings_for(file_path, language), *span)
            if len(self._bound_names) >= _BODY_TYPE_CACHE_ENTRIES:
                self._bound_names.clear()
            self._bound_names[key] = names
        return names

    def _bindings_for(self, file_path: str, language: str) -> tuple[tuple[int, str], ...]:
        """Every name one file binds, scanned once however many bodies ask."""
        found = self._bindings.get(file_path)
        if found is None:
            found = scan_bindings(self._text_of(file_path), language)
            if len(self._bindings) >= _SOURCE_CACHE_FILES:
                self._bindings.clear()
            self._bindings[file_path] = found
        return found

    def _framework_names(self, language: str) -> frozenset[str]:
        """Every name a framework decorator retypes anywhere in the repo."""
        if self._framework_name_set is None:
            found: set[str] = set()
            for parsed in self._parsed_files.values():
                if parsed.file_info.language != language:
                    continue
                for symbol in parsed.symbols:
                    if (
                        symbol.kind in _FUNCTION_KINDS
                        and not symbol.parent_name
                        and framework_decorated_type(symbol.decorators, language)
                    ):
                        found.add(symbol.name)
            self._framework_name_set = frozenset(found)
        return self._framework_name_set

    def _framework_types_in(self, file_path: str, language: str) -> dict[str, str]:
        """``{name: type}`` for one file's module-level decorated defs."""
        types = self._framework_types.get(file_path)
        if types is None:
            parsed = self._parsed_files.get(file_path)
            types = {}
            for symbol in parsed.symbols if parsed else ():
                if symbol.kind not in _FUNCTION_KINDS or symbol.parent_name:
                    continue
                type_name = framework_decorated_type(symbol.decorators, language)
                if type_name is not None:
                    types[symbol.name] = type_name
            # Uncapped, unlike the source-text caches: this holds names read off
            # already-resident symbols, and is empty for all but a few files.
            self._framework_types[file_path] = types
        return types

    def _framework_type_of(self, file_path: str, receiver_name: str, language: str) -> str | None:
        """The framework type of *receiver_name*, where this file can see it.

        Declared here, or imported here by name. A decorated def in a file the
        caller never imports is not this receiver, and reaching for it would be
        the bare-name match this tier exists to avoid.
        """
        # One pass over the repo's symbols answers for every call site that
        # names nothing decorated, which is all of them in a repo that uses no
        # framework in the table. Without it every unresolved member call pays
        # a per-file symbol walk and three dict lookups: 15% of django's build
        # for a repo that gains no edge at all.
        if receiver_name not in self._framework_names(language):
            return None

        own = self._framework_types_in(file_path, language).get(receiver_name)
        if own is not None:
            return own

        bound = self._import_names.get(file_path, {}).get(receiver_name)
        if bound is None or bound.startswith("external:"):
            return None
        binding = self._import_bindings.get(file_path, {}).get(receiver_name)
        exported = (binding.exported_name if binding else None) or receiver_name
        declaring = self._barrel_origins.get(bound, {}).get(exported) or bound
        return self._framework_types_in(declaring, language).get(exported)

    def _declarations_for(self, file_path: str, language: str) -> tuple[Declaration, ...]:
        """Every declaration in one file, scanned once however many bodies ask."""
        found = self._declarations.get(file_path)
        if found is None:
            found = scan_declarations(self._text_of(file_path), language)
            if len(self._declarations) >= _SOURCE_CACHE_FILES:
                self._declarations.clear()
            self._declarations[file_path] = found
        return found

    def _spans_for(self, file_path: str) -> dict[str, tuple[int, int]]:
        """``{symbol_id: (start_line, end_line)}`` for one file."""
        spans = self._symbol_spans.get(file_path)
        if spans is None:
            parsed = self._parsed_files.get(file_path)
            spans = {s.id: (s.start_line, s.end_line) for s in (parsed.symbols if parsed else ())}
            if len(self._symbol_spans) >= _SOURCE_CACHE_FILES:
                self._symbol_spans.clear()
            self._symbol_spans[file_path] = spans
        return spans

    def _text_of(self, file_path: str) -> str:
        """One file's source, or empty if it cannot be read."""
        text = self._source_text.get(file_path)
        if text is not None:
            return text

        parsed = self._parsed_files.get(file_path)
        text = ""
        if parsed is not None:
            try:
                text = Path(parsed.file_info.abs_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""

        if len(self._source_text) >= _SOURCE_CACHE_FILES:
            self._source_text.clear()
        self._source_text[file_path] = text
        return text


def _rivals_a_class_method(symbol_id: str) -> bool:
    """Another class's ordinary method — the one shape that proves last-wins.

    Not a top-level function (Kotlin and C++ put these beside classes, and a
    bare call may genuinely mean one) and not a constructor (name equals its
    parent's), which ``new Entry()`` should reach even inside a class nesting
    its own ``Entry``.
    """
    parts = symbol_id.split("::")
    return len(parts) >= 3 and parts[-1] != parts[-2]


def _extract_class_id(symbol_id: str) -> str | None:
    """``path::Cls::meth`` -> ``path::Cls``; None when no class encloses it.

    The heritage graph is keyed on the class's own symbol id, so the class
    *name* alone cannot be looked up in it — that is the name-keyed shortcut
    the walk exists to avoid.
    """
    parts = symbol_id.split("::")
    return "::".join(parts[:-1]) if len(parts) >= 3 else None


def _extract_class_from_symbol_id(symbol_id: str) -> str | None:
    """Extract parent class name from a symbol ID like 'path::ClassName::method'."""
    parts = symbol_id.split("::")
    if len(parts) >= 3:
        return parts[-2]
    return None
