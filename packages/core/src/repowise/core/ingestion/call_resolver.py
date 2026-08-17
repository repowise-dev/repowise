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
from pathlib import Path
from typing import Any

import structlog

from .languages.receiver_types import (
    IMPLICIT_FIELD_LANGUAGES,
    RECEIVER_TYPE_LANGUAGES,
    Declaration,
    scan_declarations,
    types_by_class,
    types_in_span,
)
from .models import (
    CallSite,
    NamedBinding,
    ParsedFile,
    ResolutionOrigin,
    symbol_id_language,
)

log = structlog.get_logger(__name__)

# Languages with an implicit method receiver, where a bare ``foo()`` binds to
# the caller's instance. Go, Python, PHP and JS/TS spell the receiver out, so a
# bare call there is a free function and this tier would resolve it wrongly.
_IMPLICIT_RECEIVER_LANGUAGES = frozenset({"java", "csharp", "cpp", "kotlin"})


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

_JVM_STRATEGIES = _LanguageCallStrategies(
    free=("_resolve_jvm_same_package",),
    member=("_resolve_jvm_receiver_same_package",),
)

_CPP_STRATEGIES = _LanguageCallStrategies(free=("_resolve_cpp_same_target",))

# Rust's crate-root strategy is deliberately absent: it runs for every language
# today, and gating it here would drop crate-name receivers in mixed repos.
_LANGUAGE_CALL_STRATEGIES: dict[str, _LanguageCallStrategies] = {
    "go": _LanguageCallStrategies(
        free=("_resolve_go_same_package",),
        member=("_resolve_go_package_call",),
    ),
    # Kotlin shares the JVM tiers but not the typed-receiver fallback: it has
    # no declaration shapes yet, so registering it would promise a resolution
    # the language gate immediately declines.
    "java": replace(_JVM_STRATEGIES, member_fallback=_TYPED_RECEIVER),
    "kotlin": _JVM_STRATEGIES,
    "csharp": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "python": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "cpp": _CPP_STRATEGIES,
    "c": _CPP_STRATEGIES,
}

_SOURCE_CACHE_FILES = 4
_BODY_TYPE_CACHE_ENTRIES = 2048


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """A call resolved to concrete symbol IDs with a confidence score."""

    caller_id: str  # symbol node ID of the calling function/method
    callee_id: str  # symbol node ID of the called function/method
    confidence: float  # 0.0–1.0
    line: int  # call site line number (for diagnostics)
    origin: ResolutionOrigin  # which strategy below produced it


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
    ) -> None:
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
        self._external_names: dict[str, frozenset[str]] = {}
        self._method_name_set: frozenset[str] | None = None

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
                if name not in file_syms:
                    self._barrel_origins[path][name] = source_file

        # Track wildcard re-exports, which forward every symbol of the imported
        # module under this file's namespace. Two shapes qualify: Rust
        # `pub use foo::*` (is_reexport) and Python/JS `from foo import *` (a
        # "*" imported name). The latter is how package ``__init__.py`` barrels
        # commonly re-export a subpackage — ``build_import_name_maps`` skips the
        # "*" name (it is not a binding), so without this pass the barrel chain
        # dead-ends one hop short of the real definition and a call through the
        # barrel resolves to nothing.
        for path, parsed in self._parsed_files.items():
            file_syms = self._file_symbols.get(path, {})
            for imp in parsed.imports:
                is_wildcard = imp.is_reexport or "*" in imp.imported_names
                if not is_wildcard or not imp.resolved_file:
                    continue
                if imp.resolved_file.startswith("external:"):
                    continue
                resolved = imp.resolved_file
                source_syms = self._file_symbols.get(resolved, {})
                for sym_name in source_syms:
                    if sym_name not in file_syms:
                        self._barrel_origins[path][sym_name] = resolved

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
        index = self._get_jvm_index()
        if index is None:
            return None
        siblings = index.same_package_files(file_path)
        for sibling in siblings:
            syms = self._file_symbols.get(sibling, {})
            sym_id = syms.get(call.target_name)
            if sym_id is not None and sym_id != caller_id:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "same_package")
        return None

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
        """
        redirects: dict[str, str] = {}
        for decl_file, decl_id, key in declarations:
            candidates = definitions.get(key, ())
            if not candidates:
                continue
            including = [
                sym_id
                for def_file, sym_id in candidates
                if decl_file in self._import_targets.get(def_file, ())
            ]
            if len(including) == 1:
                redirects[decl_id] = including[0]
            elif len(candidates) == 1:
                redirects[decl_id] = candidates[0][1]
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

    def _merged_symbols_for(self, file_path: str) -> dict[str, str]:
        """Merged ``{name → symbol_id}`` across every file *file_path* imports.

        Sorted-path merge order with first-wins gives deterministic
        precedence for names exported by multiple imports.
        """
        merged = self._merged_import_symbols.get(file_path)
        if merged is None:
            merged = {}
            for imported_file in sorted(self._import_targets.get(file_path, ())):
                if imported_file.startswith("external:"):
                    continue
                for name, sym_id in self._file_symbols.get(imported_file, {}).items():
                    merged.setdefault(name, sym_id)
            self._merged_import_symbols[file_path] = merged
        return merged

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
                call = CallSite(
                    target_name=call.target_name,
                    receiver_name=call.receiver_name,
                    caller_symbol_id=f"{file_path}::__module__",
                    line=call.line,
                    argument_count=call.argument_count,
                )

            resolved = self._resolve_one(file_path, call)
            if resolved:
                results.append(self._redirect_to_definition(resolved))

        return results

    def _resolve_one(self, file_path: str, call: CallSite) -> ResolvedCall | None:
        """Resolve a single CallSite through the three-tier fallback."""
        caller_id = call.caller_symbol_id
        assert caller_id is not None

        # --- Method call with receiver: receiver.method() ---
        if call.receiver_name:
            return self._resolve_member_call(file_path, call, caller_id)

        # --- Free function call: function() ---
        return self._resolve_free_call(file_path, call, caller_id)

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
            source_syms = self._file_symbols.get(source_file, {})
            if lookup_name in source_syms:
                return ResolvedCall(
                    caller_id, source_syms[lookup_name], 0.90, call.line, "import_scoped"
                )

        if not declared:
            return None

        # 2a fallback: plain _import_names (for imports without binding data)
        name_to_file = self._import_names.get(file_path, {})
        if target_name in name_to_file and not binding:
            source_file = name_to_file[target_name]
            barrel = self._barrel_origins.get(source_file, {})
            if target_name in barrel:
                source_file = barrel[target_name]
            source_syms = self._file_symbols.get(source_file, {})
            if target_name in source_syms:
                return ResolvedCall(
                    caller_id, source_syms[target_name], 0.90, call.line, "import_scoped"
                )

        # 2b: Check all imported files for the symbol (pre-merged lookup)
        merged_syms = self._merged_symbols_for(file_path)
        if target_name in merged_syms:
            return ResolvedCall(
                caller_id, merged_syms[target_name], 0.85, call.line, "import_merged"
            )

        # Tier 3: global unique match — only within the same language
        candidates = self._global_symbols.get(target_name, [])
        if len(candidates) == 1 and candidates[0] != caller_id:
            caller_lang = symbol_id_language(self._parsed_files, caller_id)
            callee_lang = symbol_id_language(self._parsed_files, candidates[0])
            if caller_lang and callee_lang and caller_lang != callee_lang:
                return None  # reject cross-language Tier 3 match
            return ResolvedCall(caller_id, candidates[0], 0.50, call.line, "global_unique")

        return None

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

        # A language may reach a receiver no import statement mentions: a Go
        # package alias spanning several files, a JVM class in the same package.
        for strategy in self._strategies_for(file_path).member:
            hit = getattr(self, strategy)(file_path, call, caller_id)
            if hit is not None:
                return hit

        # Strategy 1: receiver is a module alias (e.g. "import models" → "models.User()")
        module_file = self._module_aliases.get(file_path, {}).get(receiver_name)
        if module_file:
            source_syms = self._file_symbols.get(module_file, {})
            if method_name in source_syms:
                return ResolvedCall(
                    caller_id, source_syms[method_name], 0.88, call.line, "module_alias"
                )

        # Strategy 1b: receiver in import names (non-alias fallback for backward compat)
        name_to_file = self._import_names.get(file_path, {})
        if receiver_name in name_to_file and not module_file:
            source_file = name_to_file[receiver_name]
            source_syms = self._file_symbols.get(source_file, {})
            if method_name in source_syms:
                return ResolvedCall(
                    caller_id, source_syms[method_name], 0.88, call.line, "module_alias"
                )

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

        return None

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
        from_field = (
            receiver_name not in body_types and language in IMPLICIT_FIELD_LANGUAGES
        )
        if from_field:
            class_id = caller_id.rpartition("::")[0]
            type_name = (
                self._field_types_in(file_path, language).get(class_id, {}).get(receiver_name)
            )
        if type_name is None:
            return None

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if found is None:
            return None
        sym_id, tier = found
        if from_field:
            return self._field_typed_call(caller_id, sym_id, tier, call.line)
        return self._body_typed_call(caller_id, sym_id, tier, call.line)

    def _body_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the calling body."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_typed_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_typed_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_typed_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_typed_global")

    def _field_typed_call(
        self, caller_id: str, sym_id: str, tier: str, line: int
    ) -> ResolvedCall:
        """Stamp an edge whose receiver was typed from the enclosing class."""
        if tier == "same_file":
            return ResolvedCall(caller_id, sym_id, 0.93, line, "receiver_field_same_file")
        if tier == "same_package":
            return ResolvedCall(caller_id, sym_id, 0.90, line, "receiver_field_same_package")
        if tier == "import":
            return ResolvedCall(caller_id, sym_id, 0.88, line, "receiver_field_import")
        return ResolvedCall(caller_id, sym_id, 0.75, line, "receiver_field_global")

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
        """
        names = self._external_names.get(file_path)
        if names is not None:
            return names

        parsed = self._parsed_files.get(file_path)
        found: set[str] = set()
        for imp in parsed.imports if parsed else ():
            if imp.resolved_file and not imp.resolved_file.startswith("external:"):
                continue
            bound = (*imp.imported_names, imp.module_path.rsplit(".", 1)[-1])
            found.update(name for name in bound if name and name != "*")

        names = frozenset(found)
        if len(self._external_names) >= _SOURCE_CACHE_FILES:
            self._external_names.clear()
        self._external_names[file_path] = names
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
        class_spans = {
            s.id: (s.start_line, s.end_line) for s in symbols if s.kind in _TYPE_KINDS
        }
        by_class = types_by_class(
            self._declarations_for(file_path, language),
            class_spans,
            [(s.start_line, s.end_line) for s in symbols if s.kind in _FUNCTION_KINDS],
        )
        if len(self._field_types) >= _SOURCE_CACHE_FILES:
            self._field_types.clear()
        self._field_types[file_path] = by_class
        return by_class

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
                text = Path(parsed.file_info.abs_path).read_text(
                    encoding="utf-8", errors="ignore"
                )
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


def _extract_class_from_symbol_id(symbol_id: str) -> str | None:
    """Extract parent class name from a symbol ID like 'path::ClassName::method'."""
    parts = symbol_id.split("::")
    if len(parts) >= 3:
        return parts[-2]
    return None
