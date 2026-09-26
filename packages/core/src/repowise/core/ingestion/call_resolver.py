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
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

import structlog

# Re-exported: the strategy table is asserted on through this module.
from .call_language_strategies import _LANGUAGE_CALL_STRATEGIES as _LANGUAGE_CALL_STRATEGIES
from .call_language_strategies import LanguageStrategiesMixin, _LanguageCallStrategies
from .call_receiver_typing import (
    _SOURCE_CACHE_FILES,
    _TYPE_KINDS,
    ReceiverTypingMixin,
)
from .language_data import (
    get_builtin_methods,
    get_external_receiver_types,
    get_external_return_types,
)
from .languages.receiver_types import Declaration
from .models import (
    CallReceiver,
    CallSite,
    NamedBinding,
    ParsedFile,
    symbol_id_language,
)
from .resolved_call import ResolvedCall
from .return_types import declared_return_type, normalize_return_type, signature_parameter_count
from .type_names import (
    csharp_extension_receiver,
    is_resolvable_type_name,
)

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

# Phase admission is intentionally explicit. P16 lands the behavior-preserving
# substrate with no language enabled; later phases add only measured lanes.
PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES: frozenset[str] = frozenset({"cpp"})



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


class CallResolver(LanguageStrategiesMixin, ReceiverTypingMixin):
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

        # C# extension methods, keyed on the type they extend rather than on
        # their holder class: {(type, method): (file_path, symbol_id)} and the
        # per-file view of the same. Held apart from the method index on
        # purpose — merging them would leak C# keys into the C++ and JVM tiers
        # that read ``_global_methods``, and would let an extension answer a
        # site the instance method the language dispatches to should own.
        self._extension_methods: dict[tuple[str, str], tuple[str, str]] = {}
        self._file_extension_methods: dict[str, dict[tuple[str, str], str]] = {}
        self._merged_import_extensions: dict[str, dict[tuple[str, str], str]] = {}

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
        # Narrowed to C# for the extension index: the set above is a bare
        # cross-language name match, so a type of that name in any language
        # would admit an extension on the BCL type it shadows.
        self._csharp_type_names = frozenset(
            symbol.name
            for symbol in self._symbols_by_id.values()
            if symbol.kind in _TYPE_KINDS and symbol.language == "csharp"
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

    def _collapse_declarations(self, sym_ids: list[str]) -> set[str]:
        """Fold each declaration onto the definition it was paired with.

        Two ids naming one symbol must not read as an ambiguity.
        """
        return {self._decl_to_def.get(sym_id, sym_id) for sym_id in sym_ids}

    def _build_indices(self, parsed_files: dict[str, ParsedFile]) -> None:
        """Build symbol lookup indices from parsed file data.

        (Import-name maps are shared — see ``import_index.build_import_name_maps``.)
        """
        # (parent, name) → [(file, symbol_id)] over symbols that carry a body,
        # and the bodiless declarations waiting to be paired against them.
        # Both feed ``_link_declarations`` once every file has been indexed.
        definitions: dict[tuple[str | None, str], list[tuple[str, str]]] = defaultdict(list)
        declarations: list[tuple[str, str, tuple[str | None, str]]] = []
        # (extended type, method) -> the symbols claiming it. Settled after the
        # loop, because ambiguity is judged repo-wide.
        extensions: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)

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

                    extended = (
                        csharp_extension_receiver(sym.signature)
                        if sym.language == "csharp"
                        else None
                    )
                    if (
                        extended is not None
                        and is_resolvable_type_name(extended, "csharp")
                        and extended in self._csharp_type_names
                    ):
                        extensions[(extended, sym.name)].add((path, sym.id))

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
        self._index_extension_methods(extensions)

    def _index_extension_methods(
        self, candidates: dict[tuple[str, str], set[tuple[str, str]]]
    ) -> None:
        """Record every unambiguous extension pair; drop the rest.

        Two holder classes declaring one ``(type, method)`` are told apart by
        which ``using`` is in scope, which the graph does not model. Refusing
        costs an edge; guessing costs correctness.

        An overload set is not this case -- every overload of one method in one
        class shares a symbol id. A ``partial`` class split across files is,
        and stays refused.
        """
        for key, sites in candidates.items():
            if len({sym_id for _, sym_id in sites}) != 1:
                continue
            path, sym_id = next(iter(sites))
            self._extension_methods[key] = (path, sym_id)
            self._file_extension_methods.setdefault(path, {})[key] = sym_id

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

    def _published_by(self, file_path: str, owner: str, name: str) -> str | None:
        """What *file_path* publishes under *name*, owned by *owner* where it can be.

        ``_file_symbols`` is flat and last-wins, so a file that declares ``new``
        on four types answers every ``Type::new()`` lookup with whichever came
        last. That is the right file and the wrong owner. ``_file_methods``
        already carries the owner, and until now was only ever asked about the
        caller's own file.

        A real module qualifier owns nothing — ``config::limits()`` has no
        ``(config, limits)`` entry anywhere — so it falls through to the flat
        lookup unchanged. This can only re-point an edge that was already
        landing on the wrong owner of the right file.
        """
        owned = self._file_methods.get(file_path, {}).get((owner, name))
        if owned is not None:
            return owned
        return self._published(file_path, name)

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
        return self._merged_over(file_path, self._file_methods, self._merged_import_methods)

    def _merged_over(
        self,
        file_path: str,
        per_file: dict[str, dict[tuple[str, str], str]],
        cache: dict[str, dict[tuple[str, str], str]],
    ) -> dict[tuple[str, str], str]:
        """One import-merged view over a per-file ``(pair → symbol_id)`` index.

        First import wins, in sorted order, so the merge is deterministic.
        """
        merged = cache.get(file_path)
        if merged is None:
            merged = {}
            for imported_file in sorted(self._import_targets.get(file_path, ())):
                if imported_file.startswith("external:"):
                    continue
                for key, sym_id in per_file.get(imported_file, {}).items():
                    merged.setdefault(key, sym_id)
            cache[file_path] = merged
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
        #
        # A std-library name is refused for the same reason tier 3 refuses it:
        # the name is in scope in every file without an import, so a repo
        # symbol that merely shares it is not what the call site named. Being
        # reachable through an import says nothing, because the guess never
        # attributed the name to one imported file in the first place.
        if (
            target_name in merged_syms
            and merged_syms[target_name] not in self._non_callable_ids
            and target_name not in get_builtin_methods(self._language_of(file_path) or "")
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
            published = self._published_by(module_file, receiver_name, method_name)
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
            published = self._published_by(source_file, receiver_name, method_name)
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
