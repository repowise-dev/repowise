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
from .call_language_strategies import LanguageStrategiesMixin
from .call_receiver_typing import (
    _SOURCE_CACHE_FILES,
    _TYPE_KINDS,
    ReceiverTypingMixin,
    _store_capped,
)
from .language_data import (
    get_builtin_methods,
    get_builtin_types,
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
from .resolvers.cpp import _SOURCE_TU_EXTS
from .return_types import (
    declared_return_type,
    normalize_return_type,
    signature_parameter_count,
    signature_parameter_range,
)
from .symbol_identity import id_segment_name, split_symbol_id
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
# C# is present: the tier answers with a name's overload set, and
# ``resolve_file`` narrows it to the member the argument count names.
_INHERITED_LANGUAGES = frozenset({"kotlin", "python", "typescript", "swift", "csharp"})

# VB.NET's own-instance receivers, lowercased: the language is case-insensitive.
_VBNET_SELF_RECEIVERS = frozenset({"me", "myclass"})

# Languages where a bare name is scoped lexically: it can only mean the
# caller's own module, an explicit ``import`` or ``open``, or the prelude, so
# repo-wide uniqueness is no evidence and only wildcard imports may merge names.
_LEXICAL_BARE_NAME_LANGUAGES = frozenset({"elixir", "fsharp"})

# Languages whose imports name what they bring into scope, so only wildcard
# imports may merge an imported file's names: ``use a::{B, C}`` binds B and C,
# never the rest of ``a``. Rust is not lexical above: its receiver-less call
# sites are mostly chained method calls, which repo-wide uniqueness still serves.
_NAMED_IMPORT_SCOPE_LANGUAGES = _LEXICAL_BARE_NAME_LANGUAGES | {"rust"}

# The sentinel an import that binds a whole module's public names carries.
_WILDCARD_IMPORTED_NAMES = ["*"]

# The graph's prefix for an import target outside the repository. Also marks a
# Python base class the repository does not declare, in an MRO walk.
_EXTERNAL_PREFIX = "external:"

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
PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES: frozenset[str] = frozenset({"cpp", "csharp", "go"})

# Chain lanes that need a file or import/re-export identity for the head type:
# a repository-global simple type name is not a language binding.
_BOUND_CHAIN_LANGUAGES = frozenset({"java", "csharp", "typescript", "go"})


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


def _has_internal_linkage(path: str, sym: Symbol) -> bool:
    """Is *sym* a C/C++ free symbol only its own translation unit can name?

    The parser records ``static`` and anonymous-namespace linkage as
    ``private``. A header's copy is compiled into every file that includes it,
    so only a source file keeps the symbol to itself.
    """
    return (
        sym.language in ("c", "cpp", "objectivec")
        and sym.parent_name is None
        and sym.visibility == "private"
        and path.lower().endswith(_SOURCE_TU_EXTS)
    )


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
        partial_fragments: dict[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> None:
        # {type symbol id: parent type symbol ids}, from the caller's already
        # resolved heritage. Absent when the resolver is built standalone, in
        # which case the inherited tier simply never fires.
        self._heritage_parents: dict[str, set[str]] = heritage_parents or {}
        # {(file, partial type name): files declaring a fragment of it}, from
        # the builder's C#/VB.NET partial pass. Empty in every other language.
        self._partial_fragments = partial_fragments or {}
        self._return_type_chain_languages = (
            PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
            if return_type_chain_languages is None
            else return_type_chain_languages
        )
        self._ancestors: dict[str, tuple[str, ...]] = {}
        self._mros: dict[str, tuple[str, ...] | None] = {}
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

        # Overload sets whose members have their own ids. Every index above
        # holds a set by its representative, the first declared member, so the
        # tiers answer exactly as for one id and ``resolve_file`` narrows the
        # answer by argument count: {member id: representative} and
        # {representative: ((member id, (fewest, most) arguments), ...)}.
        self._overload_rep: dict[str, str] = {}
        self._overload_members: dict[str, tuple[tuple[str, tuple[int, int | None]], ...]] = {}
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
        # C/C++ symbols with internal linkage; see ``_reachable_by_name``.
        self._tu_local_ids: set[str] = set()
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

        self._init_receiver_typing_caches()
        self._repo_rebound_names: dict[str, frozenset[str]] = {}
        # {file: {(line, receiver, target): the call it is chained onto}}, and
        # the chain links being typed right now, which a cycle would revisit.
        self._chained_sites: dict[str, dict[tuple[int, str | None, str], CallReceiver]] = {}
        self._chain_links_in_flight: set[tuple[str, int, str | None, str]] = set()
        self._unindexed_extensions: frozenset[str] | None = None

        # Barrel re-export origins: {barrel_file: {name: origin_file}}
        self._barrel_origins: dict[str, dict[str, str]] = defaultdict(dict)

        # Keep reference for cross-language checks in Tier 3
        self._parsed_files = parsed_files

        self._init_workspace_indexes(repo_path)

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

        Two ids naming one symbol must not read as an ambiguity, and neither
        must the members of one overload set, which fold to its representative.
        """
        return {
            self._overload_rep.get(target, target)
            for target in (self._decl_to_def.get(sym_id, sym_id) for sym_id in sym_ids)
        }

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
        self._pair_overload_members()
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
        representatives = self._index_overload_sets(parsed)

        for sym in parsed.symbols:
            # An overload set is indexed once, under its representative.
            index_id = representatives.get(sym.id, sym.id)
            decl_key = (sym.parent_name, sym.name)
            if sym.is_declaration:
                declarations.append((path, index_id, decl_key))
                # A declaration must never displace a definition already
                # indexed under this name — a .cpp that forward-declares a
                # helper above its own body holds both.
                #
                # A method declaration stays out: this index answers
                # unqualified lookups from importing files, and no bare name
                # can legally reach a method. The (class, method) index
                # below still takes it.
                if sym.parent_name is None:
                    file_syms.setdefault(sym.name, index_id)
            else:
                definitions[decl_key].append((path, index_id))
                # File-level symbol index (top-level symbols and methods)
                file_syms[sym.name] = index_id

            # Method index: (class_name, method_name) → symbol_id
            if sym.parent_name:
                key = (sym.parent_name, sym.name)
                file_methods[key] = index_id
                self._global_methods[key].append((path, index_id))

                extended = self._csharp_extended_type(sym)
                if extended is not None:
                    extensions[(extended, sym.name)].add((path, index_id))

            self._index_globally(path, sym, index_id)

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

    def _index_globally(self, path: str, sym: Symbol, index_id: str) -> None:
        if _has_internal_linkage(path, sym):
            self._tu_local_ids.add(index_id)
        if sym.kind in _NON_CALLABLE_KINDS:
            self._non_callable_ids.add(index_id)
        if _is_property_accessor(sym):
            self._property_accessor_ids.add(index_id)
        # Same rule as the per-file index, for the global-unique tier.
        if not (sym.is_declaration and sym.parent_name is not None):
            self._global_symbols[sym.name].append(index_id)

    def _index_overload_sets(self, parsed: ParsedFile) -> dict[str, str]:
        """Record *parsed*'s overload sets; ``{member id: representative}``.

        Only parameter-count members are narrowed: a build-variant member
        (``#cfg(...)``) is not chosen by its arguments.
        """
        members: dict[str, list[Symbol]] = defaultdict(list)
        for sym in parsed.symbols:
            base, payload = split_symbol_id(sym.id)
            if payload is not None and payload.isdigit():
                members[base].append(sym)
        representatives: dict[str, str] = {}
        for overloads in members.values():
            overloads.sort(key=lambda sym: sym.start_line)
            rep = overloads[0].id
            ranges: dict[str, tuple[int, int | None]] = {}
            for sym in overloads:
                representatives[sym.id] = rep
                admitted = signature_parameter_range(sym.signature or "", sym.language)
                if admitted is not None:
                    ranges.setdefault(sym.id, admitted)
            self._overload_members[rep] = tuple(ranges.items())
        self._overload_rep.update(representatives)
        return representatives

    def _pair_overload_members(self) -> None:
        """Pair each member of a declared overload set with its definition.

        ``_link_declarations`` pairs a declared set with a defined one by their
        representatives; the other members pair here by parameter count, so
        each declaration records its definition. The definition also admits
        the defaults its declaration writes: C++ writes them on the header
        declaration only, so ``f(int, int)`` alone would refuse the one-argument
        call ``f(int, int = 0)`` admits.
        """
        for declared, defined in list(self._decl_to_def.items()):
            declared_members = self._overload_members.get(declared, ())
            defined_members = self._overload_members.get(defined, ())
            by_count = {split_symbol_id(member)[1]: member for member, _ in defined_members}
            floors: dict[str, int] = {}
            for member, (fewest, _most) in declared_members:
                target = by_count.get(split_symbol_id(member)[1])
                if target is not None:
                    self._decl_to_def.setdefault(member, target)
                    floors[target] = min(fewest, floors.get(target, fewest))
            if floors:
                self._overload_members[defined] = tuple(
                    (member, (min(fewest, floors.get(member, fewest)), most))
                    for member, (fewest, most) in defined_members
                )

    def _index_extension_methods(
        self, candidates: dict[tuple[str, str], set[tuple[str, str]]]
    ) -> None:
        """Record every unambiguous extension pair; drop the rest.

        Two holder classes declaring one ``(type, method)`` are told apart by
        which ``using`` is in scope, which the graph does not model. Refusing
        costs an edge; guessing costs correctness.

        An overload set is not this case -- every overload of one method in one
        class is indexed under one representative id. A ``partial`` class split
        across files is, and stays refused.
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
            # An overload stub's implementation shares its id: already defined.
            if not candidates or any(sym_id == decl_id for _f, sym_id in candidates):
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
        a language in ``_NAMED_IMPORT_SCOPE_LANGUAGES`` it is knowable and it
        matters, so only imports that bind a whole module's public names count.
        """
        targets = self._import_targets.get(file_path, set())
        if self._language_of(file_path) not in _NAMED_IMPORT_SCOPE_LANGUAGES:
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

            resolved = self._overload_sibling_call(call) or self._resolve_one(file_path, call)
            if resolved:
                resolved = self._narrow_overload(self._redirect_to_definition(resolved), call)
            if resolved:
                # The edge type is a property of the call syntax, not of the
                # tier that answered, so it is stamped once here.
                if call.edge_type != "calls":
                    resolved = replace(resolved, edge_type=call.edge_type)
                if call.spawned:
                    resolved = replace(resolved, spawned=True)
                results.append(resolved)

        return results

    def _narrow_overload(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """Move an edge on an overload set onto the member its arguments call.

        Every tier answers with the set's representative; this is the one place
        a member is chosen. A count no member admits, a tie, or an unknown count
        (a method reference, a C# method group) keeps the representative. An
        overload calling itself is recursion, which draws no edge.
        """
        rep = self._overload_rep.get(resolved.callee_id)
        if rep is None:
            return resolved
        target = self._pick_overload(rep, call.argument_count) or rep
        if target == resolved.caller_id:
            return None
        return resolved if target == resolved.callee_id else replace(resolved, callee_id=target)

    def _pick_overload(self, rep: str, argument_count: int | None) -> str | None:
        """The one member of *rep*'s set a call with *argument_count* arguments means.

        Asked in the order the languages apply: a member of exactly that arity,
        then one whose defaults admit the count (Java's first phase, C#'s normal
        form), then one whose varargs or ``params`` expand to it. The first
        phase with any candidate decides, and two candidates there are a tie.
        """
        if argument_count is None:
            return None
        members = self._overload_members.get(rep, ())
        phases = (
            [m for m, (fewest, most) in members if fewest == most == argument_count],
            [m for m, (fewest, most) in members if most is not None and fewest <= argument_count <= most],
            [m for m, (fewest, most) in members if most is None and fewest <= argument_count],
        )
        found = next((phase for phase in phases if phase), [])
        return found[0] if len(found) == 1 else None

    def _overload_sibling_call(self, call: CallSite) -> ResolvedCall | None:
        """An overload calling another member of its own set, ``f(a)`` inside ``f(a, b)``.

        The tiers answer such a call with the set's representative, which is
        refused as recursion when the caller is that representative itself.
        """
        caller_id = call.caller_symbol_id or ""
        rep = self._overload_rep.get(caller_id)
        if rep is None or call.receiver_name not in (None, "this", "self"):
            return None
        if self._symbols_by_id[caller_id].name != call.target_name:
            return None
        target = self._pick_overload(rep, call.argument_count)
        if target is None or target == caller_id:
            return None
        return ResolvedCall(caller_id, target, 0.95, call.line, "same_file")

    def _resolve_one(self, file_path: str, call: CallSite) -> ResolvedCall | None:
        """Resolve a single CallSite through the three-tier fallback."""
        caller_id = call.caller_symbol_id
        assert caller_id is not None

        language = self._language_of(file_path) or ""
        receiver_call = call.receiver_call
        if language == "python" and _is_super_receiver(receiver_call):
            return self._with_props(self._super_call(call, caller_id), call)
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
            handled, hit = self._resolve_member_call(file_path, call, caller_id)
            if not handled and call.bare_name_fallback:
                hit = self._resolve_free_call(file_path, call, caller_id)
            return self._with_props(hit, call)

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
        inner_callee = (
            None
            if tabled is not None
            else self._chain_inner_callee(file_path, call, inner, caller_id, language)
        )
        if language == "go" and inner_callee is not None:
            # A go method may sit in any file of its type's package, so the
            # type is read by identity and the method asked for across it.
            type_id = self._returned_type_id(inner_callee)
            hit = (
                None
                if type_id is None
                else self._call_typed_receiver(file_path, call, caller_id, type_id)
            )
            if hit is not None:
                tier = hit.origin.removeprefix("receiver_typed_")
                return True, self._return_typed_call(caller_id, hit.callee_id, tier, call.line)
        type_name = (
            tabled
            if tabled is not None
            else self._callee_chain_return_type(inner_callee, inner, language)
        )
        if type_name is None:
            return False, None

        found = self._typed_receiver_target(file_path, call, caller_id, type_name)
        if found is None and language == "csharp":
            return self._csharp_chain_member(file_path, call, caller_id, type_name)
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
        if language == "go":
            # A go method is declared in its receiver type's package and go has
            # no extension methods, so a head type with no repository method,
            # declared here or not, leaves nothing for the bare name to find.
            return True
        if language in ("csharp", "typescript"):
            return False
        return type_name in self._known_type_names

    def _csharp_chain_member(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        type_name: str,
    ) -> tuple[bool, ResolvedCall | None]:
        """The member a C# chain reaches on a head type that does not declare it.

        C# binds an instance method, an inherited one included, before an
        extension, and an extension on a base type serves every type deriving
        from it. Ancestors on two branches answering is ambiguous, and refused.

        With no answer the bare-name fallback is disproved for a known type,
        unless an extension the index leaves out carries the name: one on a
        type parameter (``this TBuilder b``) or an external type may be it.
        """
        type_id = self._csharp_type_id(type_name)
        if type_id is not None:
            inherited = self._ancestor_method(type_id, call.target_name, caller_id)
            if inherited is not None:
                same_file = self._symbol_paths_by_id.get(inherited) == file_path
                tier = "same_file" if same_file else "import"
                return True, self._return_typed_call(caller_id, inherited, tier, call.line)

        own = self._typed_extension_call(file_path, call, caller_id, type_name, "csharp")
        if own is not None:
            return True, own
        ancestor_names = [
            symbol.name
            for ancestor in (self._ancestors_of(type_id) if type_id is not None else ())
            if (symbol := self._symbols_by_id.get(ancestor)) is not None
        ]
        hits = {}
        for name in ancestor_names:
            hit = self._typed_extension_call(file_path, call, caller_id, name, "csharp")
            if hit is not None:
                hits[hit.callee_id] = hit
        if hits:
            return True, next(iter(hits.values())) if len(hits) == 1 else None
        # Proven only for a type whose members were all asked: one whose every
        # base is a repository type, or a builtin. A name the repository does
        # not declare may be a type parameter (``TBuilder``), whose members are
        # its constraint's.
        known = (
            self._lineage_is_in_repo(type_id)
            if type_id is not None
            else id_segment_name(type_name) in get_builtin_types("csharp")
        )
        return known and call.target_name not in self._unindexed_extension_names(), None

    def _lineage_is_in_repo(self, class_id: str) -> bool:
        """Does every base of *class_id* and of its ancestors resolve to a repository type?"""
        for type_id in (class_id, *self._ancestors_of(class_id)):
            bases = self._declared_bases(type_id)
            if bases is None or any(base.startswith(_EXTERNAL_PREFIX) for base in bases):
                return False
        return True

    def _csharp_type_id(self, type_name: str) -> str | None:
        """The one C# type *type_name* (``IFoo`1`` or ``IFoo``) names repo-wide, or None."""
        bare, _, arity = type_name.partition("`")
        candidates = [
            symbol
            for sym_id in self._global_symbols.get(bare, ())
            if (symbol := self._symbols_by_id[sym_id]).kind in _TYPE_KINDS
            and symbol.language == "csharp"
            and (symbol.type_parameter_count or 0) == int(arity or 0)
        ]
        return candidates[0].id if len(candidates) == 1 else None

    def _unindexed_extension_names(self) -> frozenset[str]:
        """Names of C# extension methods the extension index leaves out; built once.

        Those extend a type parameter or an external type, so no typed lookup
        can reach them and only the bare name still can.
        """
        if self._unindexed_extensions is None:
            self._unindexed_extensions = frozenset(
                sym.name
                for sym in self._symbols_by_id.values()
                if sym.language == "csharp"
                and csharp_extension_receiver(sym.signature)
                and self._csharp_extended_type(sym) is None
            )
        return self._unindexed_extensions

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

    def _chain_inner_callee(
        self,
        file_path: str,
        call: CallSite,
        inner: CallReceiver,
        caller_id: str,
        language: str,
    ) -> str | None:
        """The repository symbol the inner call of a chain resolves to."""
        if language not in self._return_type_chain_languages:
            # Admitted by its table alone; inferring from repository return
            # types is not admitted for this language.
            return None
        link = (file_path, call.line, inner.receiver_name, inner.target_name)
        if link in self._chain_links_in_flight:
            return None
        inner_call = CallSite(
            target_name=inner.target_name,
            receiver_name=inner.receiver_name,
            caller_symbol_id=caller_id,
            line=call.line,
            argument_count=inner.argument_count,
            receiver_call=self._nested_receiver_call(file_path, call.line, inner, language),
        )
        self._chain_links_in_flight.add(link)
        try:
            resolved_inner = self._resolve_one(file_path, inner_call)
        finally:
            self._chain_links_in_flight.discard(link)
        if resolved_inner is None:
            return None
        # The overload the inner call's arguments select is the one whose type it returns.
        callee_id = resolved_inner.callee_id
        rep = self._overload_rep.get(callee_id)
        if rep is not None:
            callee_id = self._pick_overload(rep, inner.argument_count) or callee_id
        return callee_id

    def _callee_chain_return_type(
        self, callee_id: str | None, inner: CallReceiver, language: str
    ) -> str | None:
        if callee_id is None:
            return None
        return self._callee_return_type(callee_id, inner.argument_count, language)

    def _nested_receiver_call(
        self, file_path: str, line: int, inner: CallReceiver, language: str
    ) -> CallReceiver | None:
        """The call *inner* is itself chained onto: ``a()`` under ``a().b().c()``.

        A receiver records one hop, but the parser keeps every link of a chain
        as its own site on the chain's first line, so the hop below is read
        off the site *inner* names. Only C# reads it; elsewhere an inner call
        is still typed as if nothing were chained under it. Go reads it too, so a
        builder chain types each link from the one before.
        """
        if language not in ("csharp", "go"):
            return None
        sites = self._chained_sites.get(file_path)
        if sites is None:
            parsed = self._parsed_files.get(file_path)
            sites = {
                (c.line, c.receiver_name, c.target_name): c.receiver_call
                for c in (parsed.calls if parsed else ())
                if c.receiver_call is not None
            }
            _store_capped(self._chained_sites, file_path, sites, _SOURCE_CACHE_FILES)
        return sites.get((line, inner.receiver_name, inner.target_name))

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
        # Overloads the arguments may mean, and declarations sharing this id
        # (an extension is called one argument short of its parameters).
        for count in {argument_count, signature_parameter_count(symbol.signature or "")}:
            key = (symbol_path, symbol.parent_name, symbol.name, count)
            if len(self._overload_return_types.get(key, ())) > 1:
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

    def _enclosing_class_method(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> str | None:
        """The caller's own class's method of this name, or None.

        ``_file_symbols`` is flat and last-wins, so a bare ``foo()`` inside
        class ``A`` would bind to class ``B``'s ``foo`` when ``B`` came later in
        the file. ``_file_methods`` carries the class. A member call on its bare
        fallback has an explicit receiver, so the caller's class is not it.
        """
        parsed = self._parsed_files.get(file_path)
        if parsed is None or parsed.file_info.language not in _IMPLICIT_RECEIVER_LANGUAGES:
            return None
        if call.receiver_name:
            return None
        caller_class = _extract_class_from_symbol_id(caller_id)
        if not caller_class:
            return None
        return self._file_methods.get(file_path, {}).get((caller_class, call.target_name))

    def _partial_fragment_member(self, file_path: str, caller_id: str, name: str) -> str | None:
        """The caller's class member *name* declared in another fragment of it.

        Every fragment of a C#/VB.NET ``partial`` type is one class scope, so a
        member (nested types included) that any fragment declares is visible by
        bare name in all of them. The lookup is keyed on the caller's file and
        class, so a same-named member of an unrelated class never answers.
        Fragments are asked in path order; an overload set split across them
        binds to the first. A nested type answers with its constructor when it
        declares one, as ``new X(..)`` does within a single file.
        """
        caller_class = _extract_class_from_symbol_id(caller_id)
        if not caller_class:
            return None
        for fragment in self._partial_fragments.get((file_path, caller_class), ()):
            if fragment == file_path:
                continue
            methods = self._file_methods.get(fragment, {})
            sym_id = methods.get((caller_class, name))
            if sym_id is None or sym_id == caller_id:
                continue
            if self._symbols_by_id[sym_id].kind in _TYPE_KINDS:
                return methods.get((name, name), sym_id)
            # A call site does not record ``new``, so ``new FaultGenerator()``
            # and a member method ``FaultGenerator()`` look alike. When the
            # repo also declares a type of that name, the method is a guess.
            if name in self._csharp_type_names:
                return None
            return sym_id
        return None

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
        # A parameter or local of that name hides every tier below.
        if self._shadowed_by_local(file_path, call, caller_id):
            return None

        # Tier 1: same-file
        handled, resolved = self._same_file_free_call(file_path, call, caller_id)
        if handled:
            return resolved
        # Still the caller's own class, declared in a sibling partial fragment.
        # Not asked when this file declares the name itself: Tier 1 declines a
        # call into the caller's own overload set (one id), and a same-named
        # overload in another fragment is no better evidence than that. Nor for a
        # member call on its bare fallback: its receiver is not the caller's class.
        if not call.receiver_name and target_name not in self._file_symbols.get(file_path, {}):
            sym_id = self._partial_fragment_member(file_path, caller_id, target_name)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.95, call.line, "enclosing_class")

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
        # A Python name imported from outside the repository is that module's,
        # not a same-named repo symbol a later tier would find. Python only:
        # Python resolves a repo's own absolute imports by dotted path, while
        # another language's import of the repo's own package by its published
        # name may still be marked external and mean repo code. Ceiling: an
        # in-repo ``except ImportError:`` fallback definition is not linked.
        if (
            binding is not None
            and (binding.source_file or "").startswith(_EXTERNAL_PREFIX)
            and self._language_of(file_path) == "python"
        ):
            return None
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
        # Uniqueness is judged before filtering data members, on purpose: filtering
        # them out first would re-uniquify a name a field and a method share. A
        # symbol the caller cannot name at all is no rival, so that one is dropped.
        candidates = [
            sym_id
            for sym_id in self._global_symbols.get(target_name, ())
            if self._reachable_by_name(file_path, sym_id)
        ]
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

    def _reachable_by_name(self, file_path: str, sym_id: str) -> bool:
        """Can a bare name in *file_path* reach *sym_id* with no include in between?

        Not when *sym_id* has internal linkage in another translation unit: a
        ``static`` function in one .c file cannot be linked from any other. The
        include-grounded tiers need no check, since an included file is part of
        the includer's translation unit.
        """
        return sym_id not in self._tu_local_ids or self._symbol_paths_by_id.get(sym_id) == file_path

    def _implicit_inherited_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """A bare call an ancestor of the caller's class answers.

        Asked after tier 3, so it can only add an edge. A member call on its
        bare fallback is refused, as in ``_enclosing_class_method``.
        """
        lang = self._language_of(file_path)
        if lang not in _IMPLICIT_RECEIVER_LANGUAGES or lang not in _INHERITED_LANGUAGES:
            return None
        if call.receiver_name:
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
    ) -> tuple[bool, ResolvedCall | None]:
        """Resolve receiver.method() calls, as ``(handled, edge)``.

        A handled None is a refusal (the receiver's type is foreign), which a
        bare-name fallback must not overrule.
        """
        receiver_name = call.receiver_name
        method_name = call.target_name
        assert receiver_name is not None

        # Every strategy below ends in a lookup keyed on the method name, so a
        # name the repo declares nowhere cannot resolve.
        if method_name not in self._global_symbols:
            return False, None

        # The caller's own file first, ahead of the language strategies: a
        # private inner class here outranks a same-named package sibling.
        own_file = self._file_methods.get(file_path, {}).get((receiver_name, method_name))
        if own_file is not None and own_file != caller_id:
            return True, ResolvedCall(caller_id, own_file, 0.93, call.line, "receiver_same_file")

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
            return True, hit

        handled, hit = self._receiver_class_call(file_path, call, caller_id)
        if handled:
            return True, hit

        hit = self._unclassed_receiver_call(file_path, call, caller_id)
        if hit is None and self._receiver_type_is_external(file_path, call, caller_id):
            # The method is the external type's: refused, not left to a name match.
            return True, None
        return hit is not None, hit

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

        Only the caller's own file, or a sibling fragment of a partial class,
        can hold the match, so index straight into those instead of scanning
        every file's method dict.
        """
        if not self._is_self_receiver(file_path, call.receiver_name):
            return None
        caller_class = _extract_class_from_symbol_id(caller_id)
        if not caller_class:
            return None
        sym_id = self._file_methods.get(file_path, {}).get(
            (caller_class, call.target_name)
        ) or self._partial_fragment_member(file_path, caller_id, call.target_name)
        if sym_id is None or sym_id == caller_id:
            return None
        return ResolvedCall(caller_id, sym_id, 0.95, call.line, "self_scope")

    def _is_self_receiver(self, file_path: str, receiver_name: str | None) -> bool:
        """Whether a receiver names the enclosing class's own instance.

        VB.NET spells it ``Me``, and ``MyClass`` for a call that skips an
        override, in any case since the language is case-insensitive. Asked
        of VB.NET files only: elsewhere ``Me`` is an ordinary identifier.
        """
        if receiver_name in ("self", "this"):
            return True
        return (
            receiver_name is not None
            and receiver_name.lower() in _VBNET_SELF_RECEIVERS
            and self._language_of(file_path) == "vbnet"
        )

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

    def _super_call(self, call: CallSite, caller_id: str) -> ResolvedCall | None:
        """``super().m()``: the first class after the caller's own in its MRO to declare ``m``.

        Never handed to the bare-name tiers, which answered with whichever
        same-named method the file declared last. A base outside the
        repository ends the walk unresolved: it may declare ``m`` itself.

        Ceiling: ``super(C, self)`` is read as the caller's own class, since
        the call site keeps no argument text. A ``C`` naming another class
        starts the walk in the wrong place; reading the first argument would
        close it.
        """
        class_id = _extract_class_id(caller_id)
        mro = self._mro(class_id) if class_id is not None else None
        for ancestor in (mro or ())[1:]:
            if ancestor.startswith(_EXTERNAL_PREFIX):
                return None
            sym_id = self._declares(ancestor, call.target_name)
            if sym_id is not None:
                return ResolvedCall(caller_id, sym_id, 0.90, call.line, "self_inherited")
        return None

    def _mro(self, class_id: str, visiting: frozenset[str] = frozenset()) -> tuple[str, ...] | None:
        """Python's C3 linearization of *class_id*, or None when it cannot be built.

        A base outside the repository is kept as an opaque leaf, so the walk
        can tell where in the order it sits without knowing its own bases.
        """
        if class_id in self._mros:
            return self._mros[class_id]
        bases = self._declared_bases(class_id)
        mro: tuple[str, ...] | None = None
        if bases is not None and class_id not in visiting:
            inner = visiting | {class_id}
            chains: list[tuple[str, ...]] = []
            for base in bases:
                external = base.startswith(_EXTERNAL_PREFIX)
                chain = (base,) if external else self._mro(base, inner)
                if chain is None:
                    break
                chains.append(chain)
            else:
                merged = _c3_merge([*chains, tuple(bases)])
                mro = None if merged is None else (class_id, *merged)
        self._mros[class_id] = mro
        return mro

    def _declared_bases(self, class_id: str) -> list[str] | None:
        """*class_id*'s bases in declaration order, each an in-repo class id or an external marker.

        The resolved heritage is an unordered id set, so each declared name is
        matched back to its id through the symbol's name, or the name an import
        alias stands for. None when a name matches two ids, or the file
        declares two classes of this name.
        """
        symbol = self._symbols_by_id.get(class_id)
        file_path = self._symbol_paths_by_id.get(class_id)
        parsed = self._parsed_files.get(file_path) if file_path else None
        if symbol is None or file_path is None or parsed is None:
            return None
        relations = [r for r in parsed.heritage if r.child_name == symbol.name]
        if len({r.line for r in relations}) > 1:
            return None
        parents = self._heritage_parents.get(class_id, ())
        bindings = self._import_bindings.get(file_path, {})
        bases: list[str] = []
        for relation in relations:
            binding = bindings.get(relation.parent_name)
            names = {relation.parent_name, binding.exported_name if binding else None}
            hits = [
                p for p in parents if (s := self._symbols_by_id.get(p)) is not None and s.name in names
            ]
            if len(hits) > 1:
                return None
            bases.append(hits[0] if hits else _EXTERNAL_PREFIX + relation.parent_name)
        return bases

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
        if _extract_class_id(caller_id) is None:
            return None
        class_id = self._caller_class_id(caller_id)
        # The caller's own class answers even when Strategy 3 declined it for
        # recursion; an ancestor's declaration of the name is not the target.
        if self._declares(class_id, method_name) is not None:
            return None
        return self._ancestor_method(class_id, method_name, caller_id)

    def _ancestor_method(self, class_id: str, method_name: str, caller_id: str) -> str | None:
        """The one method of this name *class_id*'s ancestors declare, never the caller."""
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
    return len(parts) >= 3 and id_segment_name(parts[-1]) != id_segment_name(parts[-2])


def _is_super_receiver(receiver: CallReceiver | None) -> bool:
    """Is this chained call's receiver Python's ``super(...)``?"""
    return receiver is not None and receiver.target_name == "super" and receiver.receiver_name is None


def _c3_merge(sequences: list[tuple[str, ...]]) -> list[str] | None:
    """The C3 merge step of Python's MRO; None when no consistent order exists."""
    pending = [list(seq) for seq in sequences if seq]
    merged: list[str] = []
    while pending:
        head = next(
            (seq[0] for seq in pending if not any(seq[0] in other[1:] for other in pending)),
            None,
        )
        if head is None:
            return None
        merged.append(head)
        pending = [rest for seq in pending if (rest := seq[1:] if seq[0] == head else seq)]
    return merged


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
