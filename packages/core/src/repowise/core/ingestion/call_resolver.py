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
    _store_capped,
)
from .language_data import (
    get_builtin_methods,
    get_external_receiver_types,
    get_external_return_types,
)
from .models import (
    CallReceiver,
    CallSite,
    Import,
    NamedBinding,
    ParsedFile,
    Symbol,
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
# its ancestors, for an explicit ``self``/``this`` receiver and an implicit one.
#
# C++ is absent because its heritage binds a qualified external parent to a
# same-named local type, putting unrelated siblings in one hierarchy. Java is
# absent because `java.scm`'s bare-call pattern also matches `this.field.m()`,
# which then arrives here indistinguishable from a real implicit receiver.
#
# C# is present: a name's overloads all share one symbol id, so a declaration
# line read back out of the graph may name another overload of the right id.
_INHERITED_LANGUAGES = frozenset({"kotlin", "python", "typescript", "swift", "csharp"})

# Languages where a bare name is scoped lexically: it can only mean the
# caller's own module, an explicit ``import`` or ``open``, or the prelude, so
# repo-wide uniqueness is no evidence and only wildcard imports may merge names.
_LEXICAL_BARE_NAME_LANGUAGES = frozenset({"elixir", "fsharp"})

# The sentinel an import that binds a whole module's public names carries.
_WILDCARD_IMPORTED_NAMES = ["*"]

# Ancestors within four hops: ``heritage_ancestors`` bounds expansion, not
# reach, so 3 reaches 4.
_MAX_ANCESTOR_EXPAND_DEPTH = 3


# Kinds that can never be the callee of a call, so the bare-name tiers never
# offer a data member as a function.
#
# Deliberately NOT the complement of ``_FUNCTION_KINDS``: a ``class`` is a
# constructor, a ``variable`` may be a rust tuple enum variant or a typescript
# const holding a function, and a ``type_alias`` is a Go conversion.
# ``property`` is the only kind that means "data member" and nothing else; every
# other language spells its fields ``variable``, indistinguishable by kind.
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


# Languages admitted to the full return-type chain lane; each is admitted
# explicitly, once measured.
PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES: frozenset[str] = frozenset({"cpp"})

# Chain lanes that need a file or import/re-export identity for the head type:
# a repository-global simple type name is not a language binding.
_BOUND_CHAIN_LANGUAGES = frozenset({"java", "csharp", "typescript"})


def _overload_return_types(
    parsed_files: dict[str, ParsedFile],
) -> dict[tuple[str, str | None, str, int | None], set[str]]:
    """``{(path, parent, name, parameter count): normalised return types}``."""
    found: dict[tuple[str, str | None, str, int | None], set[str]] = defaultdict(set)
    for path, parsed in parsed_files.items():
        for symbol in parsed.symbols:
            raw_return = declared_return_type(symbol.signature or "")
            normalized = (
                normalize_return_type(raw_return, symbol.language) if raw_return else None
            )
            if normalized is None:
                continue
            key = (
                path,
                symbol.parent_name,
                symbol.name,
                signature_parameter_count(symbol.signature or ""),
            )
            found[key].add(normalized)
    return found


def _flattened_wildcard_source(imp: Import) -> str | None:
    """The repository file whose every name *imp* forwards, or None.

    Two shapes qualify: Rust ``pub use foo::*`` (``is_reexport``) and
    Python/JS ``from foo import *`` (a "*" imported name).
    """
    is_wildcard = imp.is_reexport or "*" in imp.imported_names
    if not is_wildcard or not imp.resolved_file:
        return None
    if imp.resolved_file.startswith("external:"):
        return None
    # ``export * as ns from "x"`` forwards the module under ``ns``,
    # so x's names are reachable as ``ns.name`` and are NOT this
    # file's own exports. Flattening them makes a bare ``name``
    # resolve into a nested namespace it was never in.
    if any(b.local_name == "*" and b.exported_name for b in imp.bindings):
        return None
    return imp.resolved_file


def _admitted_cpp_chain(type_name: str, method_name: str) -> bool:
    return type_name == "future" and method_name == "get"


def _renames_on_the_way(binding: NamedBinding | None, name: str) -> bool:
    return binding is not None and (binding.exported_name or name) != name


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
        self._overload_return_types = _overload_return_types(parsed_files)
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

        # Lazy per-file merged views of every imported file's symbol and
        # method tables, merged in sorted import order with first-wins so
        # shadowed names resolve deterministically.
        self._merged_import_symbols: dict[str, dict[str, str]] = {}
        self._merged_import_methods: dict[str, dict[tuple[str, str], str]] = {}

        # Lazy per-file set of (line, target) that also carry a receiver.
        self._member_shaped: dict[str, set[tuple[int, str]]] = {}

        self._init_receiver_typing_caches()
        self._repo_rebound_names: dict[str, frozenset[str]] = {}

        # Barrel re-export origins: {barrel_file: {name: origin_file}}
        self._barrel_origins: dict[str, dict[str, str]] = defaultdict(dict)

        # Keep reference for cross-language checks in Tier 3
        self._parsed_files = parsed_files

        self._init_workspace_indexes(repo_path)

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
        self._record_direct_barrel_origins()
        wildcard_sources = self._record_wildcard_barrel_origins()
        self._forward_barrels_over_barrels(wildcard_sources)
        self._deepen_barrel_chains()

    def _record_direct_barrel_origins(self) -> None:
        for path, name_to_file in self._import_names.items():
            file_syms = self._file_symbols.get(path, {})
            for name, source_file in name_to_file.items():
                # An origin outside the repo names no file a reader of this
                # map can look up; the wildcard pass refuses one too.
                if name not in file_syms and not source_file.startswith("external:"):
                    self._barrel_origins[path][name] = source_file

    def _record_wildcard_barrel_origins(self) -> dict[str, list[str]]:
        """Forward every name a wildcard import publishes; return each file's sources.

        This is how package ``__init__.py`` barrels commonly re-export a
        subpackage. ``build_import_name_maps`` skips the "*" name (it is not a
        binding), so without this pass the barrel chain dead-ends one hop short
        of the real definition and a call through the barrel resolves to nothing.
        """
        wildcard_sources: dict[str, list[str]] = defaultdict(list)
        for path, parsed in self._parsed_files.items():
            file_syms = self._file_symbols.get(path, {})
            for imp in parsed.imports:
                resolved = _flattened_wildcard_source(imp)
                if resolved is None:
                    continue
                if resolved != path:
                    wildcard_sources[path].append(resolved)
                self._forward_published_names(path, resolved, file_syms)
        return wildcard_sources

    def _forward_published_names(
        self, path: str, resolved: str, file_syms: dict[str, str]
    ) -> None:
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

    def _forward_barrels_over_barrels(self, wildcard_sources: dict[str, list[str]]) -> None:
        """Forward what each wildcard source re-exports too, to a fixpoint.

        The wildcard pass forwards only what the source file DECLARES, so a
        barrel over a barrel forwards nothing and the chain breaks at its
        first link rather than its last. The multi-hop pass cannot do this
        job: it deepens entries that exist, and here none do.

        Sorted so that a name two of this file's barrels both forward lands on
        the same origin whatever order the repository was walked in.
        """
        for _ in range(4):
            changed = False
            for path, sources in sorted(wildcard_sources.items()):
                if self._forward_source_origins(path, sources):
                    changed = True
            if not changed:
                break

    def _forward_source_origins(self, path: str, sources: list[str]) -> bool:
        file_syms = self._file_symbols.get(path, {})
        origins = self._barrel_origins[path]
        changed = False
        for source in sorted(sources):
            source_bindings = self._import_bindings.get(source, {})
            for name, declaring in sorted(self._barrel_origins.get(source, {}).items()):
                if name in file_syms or name in origins or declaring == path:
                    continue
                # The map records only the declaring file, not the name the
                # symbol has there, so a renaming hop could forward a key that
                # means something else at the far end. Refuse it.
                if _renames_on_the_way(source_bindings.get(name), name):
                    continue
                origins[name] = declaring
                changed = True
        return changed

    def _deepen_barrel_chains(self) -> None:
        """Multi-hop: follow chains up to 5 hops."""
        for _ in range(4):
            changed = False
            for _path, origins in list(self._barrel_origins.items()):
                if self._deepen_origins(origins):
                    changed = True
            if not changed:
                break

    def _deepen_origins(self, origins: dict[str, str]) -> bool:
        changed = False
        for name, source in list(origins.items()):
            deeper = self._barrel_origins.get(source, {}).get(name)
            if deeper and deeper != source:
                origins[name] = deeper
                changed = True
        return changed

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
            self._index_file(path, parsed, definitions, declarations, extensions)

        self._decl_to_def = self._link_declarations(declarations, definitions)
        self._index_extension_methods(extensions)

    def _index_file(
        self,
        path: str,
        parsed: ParsedFile,
        definitions: dict[tuple[str | None, str], list[tuple[str, str]]],
        declarations: list[tuple[str, str, tuple[str | None, str]]],
        extensions: dict[tuple[str, str], set[tuple[str, str]]],
    ) -> None:
        """Index one file's symbols, collecting what is settled repo-wide later."""
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

                extended = self._csharp_extended_type(sym)
                if extended is not None:
                    extensions[(extended, sym.name)].add((path, sym.id))

            self._index_globally(sym)

        self._file_symbols[path] = file_syms
        self._file_methods[path] = file_methods

    def _csharp_extended_type(self, sym: Symbol) -> str | None:
        """The repository C# type *sym* extends, when it is an extension method."""
        if sym.language != "csharp":
            return None
        extended = csharp_extension_receiver(sym.signature)
        if extended is None or not is_resolvable_type_name(extended, "csharp"):
            return None
        return extended if extended in self._csharp_type_names else None

    def _index_globally(self, sym: Symbol) -> None:
        if sym.kind in _NON_CALLABLE_KINDS:
            self._non_callable_ids.add(sym.id)
        if _is_property_accessor(sym):
            self._property_accessor_ids.add(sym.id)
        # Same rule as the per-file index, for the global-unique tier.
        if not (sym.is_declaration and sym.parent_name is not None):
            self._global_symbols[sym.name].append(sym.id)

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
        the name up in the *header's* symbol table, the file the caller
        includes, so without the pairing the definition gets no inbound edge
        and reads as dead code. ``resolve_file`` moves the edge onto it.

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
        last: the right file and the wrong owner. ``_file_methods`` carries the
        owner. A module qualifier owns nothing, so it falls through to the flat
        lookup unchanged.
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
                # The edge type is a property of the call syntax, not of the
                # tier that answered, so it is stamped once here.
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
        the bare-name fallback; an absent or external type leaves it to run.
        """

        inner = call.receiver_call
        assert inner is not None

        tabled = self._external_chain_return_type(file_path, inner, language)
        from_table = tabled is not None
        type_name = (
            tabled
            if tabled is not None
            else self._inferred_chain_return_type(file_path, call, inner, caller_id, language)
        )
        if type_name is None:
            return False, None

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if language == "cpp" and not _admitted_cpp_chain(type_name, call.target_name):
            # C++ admits only ``future.get()``; broader return-name matching
            # is not admitted.
            return False, None
        if found is None or (found[1] == "global" and language in _BOUND_CHAIN_LANGUAGES):
            return self._chain_refusal_is_proven(language, type_name, from_table), None

        sym_id, tier = found
        return True, self._return_typed_call(caller_id, sym_id, tier, call.line)

    def _chain_refusal_is_proven(self, language: str, type_name: str, from_table: bool) -> bool:
        """Whether a chain with no usable target disproves the bare-name fallback."""
        if language == "java":
            # A table type is external in this file and java has no extension
            # methods, so the repository cannot declare its method: the
            # bare-name answer is disproved, not merely unevidenced.
            return from_table
        if language in ("csharp", "typescript"):
            return False
        return type_name in self._known_type_names

    def _external_chain_return_type(
        self,
        file_path: str,
        inner: CallReceiver,
        language: str,
    ) -> str | None:
        """The table's return type for ``Type.method(..)`` at the head of a chain.

        None when the head is not a table entry, or when this file binds the
        name to something the repository owns. The bound value has to be read,
        not merely tested: an unresolved import is an ``external:`` marker.

        A repository declaring a type of that name anywhere is exempted
        outright, because java's same-package types need no import and the
        table records the external type's return type, not the repository's.
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
        if language not in self._return_type_chain_languages:
            # Admitted by its table alone; inferring from repository return
            # types is not admitted for this language.
            return None
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
        return self._callee_return_type(resolved_inner.callee_id, inner.argument_count, language)

    def _callee_return_type(
        self, callee_id: str, argument_count: int | None, language: str
    ) -> str | None:
        """The type a call to *callee_id* yields, unless its overloads disagree."""
        symbol = self._symbols_by_id.get(callee_id)
        if symbol is None:
            return None
        if symbol.kind in _TYPE_KINDS:
            return symbol.name

        raw_return = declared_return_type(symbol.signature or "")
        type_name = normalize_return_type(raw_return, language) if raw_return else None
        symbol_path = self._symbol_paths_by_id.get(callee_id)
        if symbol_path is None:
            return None
        overload_key = (
            symbol_path,
            symbol.parent_name,
            symbol.name,
            argument_count,
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
        ``foo(bar.foo())`` suppresses the tier for its own bare ``foo()``: a
        missed edge, never a wrong one.
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
        class ``A`` would bind to class ``B``'s ``foo`` when ``B`` came later in
        the file. ``_file_methods`` carries the class.
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
        handled, resolved = self._same_file_free_call(file_path, call, caller_id)
        if handled:
            return resolved

        # A language may see names no import mentions (a package sibling, a
        # C/C++ build target), and those beat the import and global tiers.
        if declared:
            hit = self._first_strategy_hit(
                self._strategies_for(file_path).free, file_path, call, caller_id
            )
            if hit is not None:
                return hit

        # Tier 2: import-scoped
        binding = self._import_bindings.get(file_path, {}).get(target_name)
        hit = self._bound_import_call(call, caller_id, binding)
        if hit is not None:
            return hit
        if not declared:
            return None
        return (
            self._named_import_call(file_path, call, caller_id, binding)
            or self._merged_import_call(file_path, call, caller_id)
            or self._repo_wide_free_call(file_path, call, caller_id)
        )

    def _first_strategy_hit(
        self,
        strategies: tuple[str, ...],
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """The first edge a named strategy resolves, in order."""
        for strategy in strategies:
            hit = getattr(self, strategy)(file_path, call, caller_id)
            if hit is not None:
                return hit
        return None

    def _same_file_free_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> tuple[bool, ResolvedCall | None]:
        """Tier 1, as ``(handled, edge)``: a handled None is a refusal."""
        callee_id = self._file_symbols.get(file_path, {}).get(call.target_name)
        if callee_id is None:
            return False, None
        own = self._enclosing_class_method(file_path, call, caller_id)
        if own is not None and own != callee_id and _rivals_a_class_method(callee_id):
            if own == caller_id:
                # Recursion the flat index handed to a stranger. No edge.
                return True, None
            return True, ResolvedCall(caller_id, own, 0.95, call.line, "enclosing_class")
        if callee_id != caller_id:  # no self-recursion edges for now
            return True, ResolvedCall(caller_id, callee_id, 0.95, call.line, "same_file")
        return False, None

    def _bound_import_call(
        self,
        call: CallSite,
        caller_id: str,
        binding: NamedBinding | None,
    ) -> ResolvedCall | None:
        """2a: the specific imported name, binding-aware."""
        if not (binding and binding.source_file):
            return None
        source_file = binding.source_file
        # Follow barrel re-export one hop
        barrel = self._barrel_origins.get(source_file, {})
        lookup_name = binding.exported_name or call.target_name
        if lookup_name in barrel:
            source_file = barrel[lookup_name]
        published = self._published(source_file, lookup_name)
        if published is None:
            return None
        return ResolvedCall(caller_id, published, 0.90, call.line, "import_scoped")

    def _named_import_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        binding: NamedBinding | None,
    ) -> ResolvedCall | None:
        """2a fallback: plain ``_import_names`` (for imports without binding data)."""
        target_name = call.target_name
        name_to_file = self._import_names.get(file_path, {})
        if target_name not in name_to_file or binding:
            return None
        source_file = name_to_file[target_name]
        barrel = self._barrel_origins.get(source_file, {})
        if target_name in barrel:
            source_file = barrel[target_name]
        published = self._published(source_file, target_name)
        if published is None:
            return None
        return ResolvedCall(caller_id, published, 0.90, call.line, "import_scoped")

    def _merged_import_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """2b: the symbol in any imported file (pre-merged lookup)."""
        target_name = call.target_name
        sym_id = self._merged_symbols_for(file_path).get(target_name)
        # A data member is not callable, here as in tier 3.
        if sym_id is None or sym_id in self._non_callable_ids:
            return None
        # A std-library name is in scope everywhere without an import, so a
        # repo symbol that merely shares it is not what the call site named.
        if target_name in get_builtin_methods(self._language_of(file_path) or ""):
            return None
        return ResolvedCall(caller_id, sym_id, 0.85, call.line, "import_merged")

    def _repo_wide_free_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Tier 3 and what follows it: answers not grounded in this file or its imports."""
        target_name = call.target_name
        # Tier 3: global unique match, only within the same language.
        # Uniqueness is judged on the unfiltered list on purpose: filtering data
        # members out first would re-uniquify a name a field and a method share.
        candidates = self._global_symbols.get(target_name, [])
        if len(candidates) == 1 and candidates[0] != caller_id:
            return self._global_unique_match(
                file_path, call, caller_id, target_name, candidates[0]
            )

        inherited = self._implicit_inherited_call(file_path, call, caller_id)
        if inherited is not None:
            return inherited

        # An overload set is several declarations under one id, not an
        # ambiguity. Asked after the inherited tier, which answers with more
        # confidence when the caller's own hierarchy declares the name.
        collapsed = self._collapse_declarations(candidates)
        if len(candidates) <= 1 or len(collapsed) != 1:
            return None
        only = next(iter(collapsed))
        if only == caller_id or only in self._property_accessor_ids:
            return None
        return self._global_unique_match(file_path, call, caller_id, target_name, only)

    def _implicit_inherited_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """A bare call an ancestor of the caller's class answers.

        Asked after tier 3, so it can only add an edge. The member-shaped
        refusal is the one ``_enclosing_class_method`` already applies: several
        grammars mint a receiver-less site for ``obj.m()`` too, and reading one
        as an implicit receiver would bind the wrong class's hierarchy to the call.
        """
        lang = self._language_of(file_path)
        if lang not in _IMPLICIT_RECEIVER_LANGUAGES or lang not in _INHERITED_LANGUAGES:
            return None
        if (call.line, call.target_name) in self._member_shaped_sites(file_path):
            return None
        sym_id = self._inherited_method(caller_id, call.target_name)
        if sym_id is None:
            return None
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "enclosing_inherited")

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
            # Refused rather than falling through, so this tier can lose an
            # edge but never gain one.
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
        # name the repo declares nowhere cannot resolve.
        if method_name not in self._global_symbols:
            return None

        # The caller's own file first, ahead of the language strategies: a
        # private inner class here outranks a same-named package sibling.
        own_file = self._file_methods.get(file_path, {}).get((receiver_name, method_name))
        if own_file is not None and own_file != caller_id:
            return ResolvedCall(caller_id, own_file, 0.93, call.line, "receiver_same_file")

        # A language may reach a receiver no import statement mentions: a Go
        # package alias spanning several files, a JVM class in the same package.
        hit = (
            self._first_strategy_hit(
                self._strategies_for(file_path).member, file_path, call, caller_id
            )
            or self._module_receiver_call(file_path, call, caller_id)
            or self._crate_root_call(call, caller_id)
        )
        if hit is not None:
            return hit

        handled, hit = self._receiver_class_call(file_path, call, caller_id)
        if handled:
            return hit

        return self._unclassed_receiver_call(file_path, call, caller_id)

    def _unclassed_receiver_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """A receiver no class name answers: ``self``/``this``, a local or a parameter.

        The fallback strategies ask only once everything above declined.
        """
        return (
            self._self_scope_call(file_path, call, caller_id)
            or self._first_strategy_hit(
                self._strategies_for(file_path).member_fallback, file_path, call, caller_id
            )
            or self._self_inherited_call(file_path, call, caller_id)
        )

    def _module_receiver_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Strategies 1 and 1b: the receiver names an imported module."""
        receiver_name = call.receiver_name
        # Strategy 1: receiver is a module alias (e.g. "import models" → "models.User()")
        module_file = self._module_aliases.get(file_path, {}).get(receiver_name)
        if module_file:
            return self._module_alias_call(module_file, call, caller_id)

        # Strategy 1b: receiver in import names (non-alias fallback for backward compat)
        name_to_file = self._import_names.get(file_path, {})
        if receiver_name not in name_to_file:
            return None
        published = self._published_by(name_to_file[receiver_name], receiver_name, call.target_name)
        if published is None:
            return None
        return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")

    def _module_alias_call(
        self,
        module_file: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        method_name = call.target_name
        published = self._published_by(module_file, call.receiver_name, method_name)
        if published is not None:
            return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")
        # A namespace over a barrel declares nothing of its own, so chase the
        # re-export map, keyed on the name the declaring file uses: the
        # re-export may rename, and the map records only the file.
        origin = self._barrel_origins.get(module_file, {}).get(method_name)
        if origin is None or origin == module_file:
            return None
        binding = self._import_bindings.get(module_file, {}).get(method_name)
        declared_name = (binding.exported_name if binding else None) or method_name
        published = self._published(origin, declared_name)
        if published is None:
            return None
        return ResolvedCall(caller_id, published, 0.88, call.line, "module_alias")

    def _crate_root_call(self, call: CallSite, caller_id: str) -> ResolvedCall | None:
        """Strategy 1c: Rust crate-scoped reference (e.g. ``my_crate::module``).

        The receiver is a crate name, the target is a symbol in that crate's lib.rs.
        """
        crate_src = self._get_rust_crate_src().get(call.receiver_name)
        if not crate_src:
            return None
        for root_file in ("lib.rs", "main.rs"):
            crate_root = f"{crate_src}/{root_file}"
            root_syms = self._file_symbols.get(crate_root, {})
            if call.target_name in root_syms:
                return ResolvedCall(
                    caller_id, root_syms[call.target_name], 0.88, call.line, "crate_root"
                )
        return None

    def _receiver_class_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> tuple[bool, ResolvedCall | None]:
        """Strategies 2 and 2b, as ``(handled, edge)``.

        The receiver names a class that declares the method: in this file, in
        an imported one, or anywhere at all.
        """
        receiver_name = call.receiver_name
        match = self._receiver_pair_match(file_path, (receiver_name, call.target_name))
        if match is None:
            return False, None
        sym_id, tier = match
        if tier == "same_file":
            return True, ResolvedCall(caller_id, sym_id, 0.93, call.line, "receiver_same_file")
        if tier == "import":
            return True, ResolvedCall(caller_id, sym_id, 0.88, call.line, "receiver_import")
        if self._answers_for_a_foreign_type(file_path, receiver_name):
            return True, None
        return True, ResolvedCall(caller_id, sym_id, 0.75, call.line, "receiver_global")

    def _self_scope_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Strategy 3: receiver is "self" or "this", so look in the same class.

        Only the caller's own file can hold the match, so index straight
        into it instead of scanning every file's method dict.
        """
        if call.receiver_name not in ("self", "this"):
            return None
        caller_class = _extract_class_from_symbol_id(caller_id)
        if not caller_class:
            return None
        sym_id = self._file_methods.get(file_path, {}).get((caller_class, call.target_name))
        if sym_id is None or sym_id == caller_id:
            return None
        return ResolvedCall(caller_id, sym_id, 0.95, call.line, "self_scope")

    def _self_inherited_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Strategy 3, continued: the method may be inherited.

        Strategy 3 can only see the caller's own class in the caller's own
        file. Asked last so it can add an edge and never displace one.
        """
        if call.receiver_name not in ("self", "this"):
            return None
        if self._language_of(file_path) not in _INHERITED_LANGUAGES:
            return None
        sym_id = self._inherited_method(caller_id, call.target_name)
        if sym_id is None:
            return None
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "self_inherited")

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
        # The caller's own class answers even when Strategy 3 declined it for
        # recursion; an ancestor's declaration of the name is not the target.
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
        in ``path``. A missed lookup would hide an ancestor from the ambiguity
        check above and let a wrong single candidate through.
        """
        file_path, _, name = class_id.partition("::")
        return self._file_methods.get(file_path, {}).get((name.rpartition("::")[2], method_name))

    def _ancestors_of(self, class_id: str) -> tuple[str, ...]:
        got = self._ancestors.get(class_id)
        if got is None:
            from .heritage_resolver import heritage_ancestors

            # Sorted: the walk expands an anchor only on its first visit, so
            # branch order decides the result and must be stable.
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
        repository that writes ``impl Trait for Vec<Entity>`` declares a
        ``Vec::new``, and without this every ``Vec::new()`` in the tree would
        bind to it. The narrower tiers are grounded in the caller's own file or
        its imports and are left alone.
        """
        if receiver_name not in get_external_receiver_types(
            self._language_of(file_path) or ""
        ):
            return False
        return receiver_name not in self._names_rebound_from_a_repo_package(file_path)

    def _names_rebound_from_a_repo_package(self, file_path: str) -> frozenset[str]:
        """Names this file imports from one of the repository's own packages.

        A file writing ``use my_crate::collections::HashMap`` means its own
        ``HashMap``, so the refusal above must not fire; only the import list
        separates that from ``use std::collections::HashMap``.

        Read off the raw import statements because a rust import resolves to no
        repository file, so ``_import_names`` cannot answer. A language with
        ``external_receiver_types`` but no workspace index would refuse where
        it should exempt.
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
        _store_capped(self._repo_rebound_names, file_path, names, _SOURCE_CACHE_FILES)
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
