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

import re
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

import structlog

# Moved helpers stay importable from here.
from .call_barrel_forwarding import (  # noqa: F401
    BarrelForwardingMixin,
    _flattened_wildcard_source,
    _renames_on_the_way,
)
from .call_cobol import CobolCallsMixin
from .call_cpp_class_scope import CppClassScopeMixin
from .call_inheritance import (  # noqa: F401
    _EXTERNAL_PREFIX,
    _MAX_ANCESTOR_EXPAND_DEPTH,
    InheritanceMixin,
    _c3_merge,
    _extract_class_id,
    _is_super_call,
)

# Re-exported: the strategy table is asserted on through this module.
from .call_language_strategies import _LANGUAGE_CALL_STRATEGIES as _LANGUAGE_CALL_STRATEGIES
from .call_language_strategies import LanguageStrategiesMixin
from .call_overload_selection import (  # noqa: F401
    _JAVA_BOXES,
    _JAVA_FINAL_PARAMETERS,
    _JAVA_JUDGED_PARAMETERS,
    _JAVA_LITERAL_LEVELS,
    _JAVA_PRIMITIVES,
    _JAVA_UNBOXED,
    _JAVA_WIDENING,
    _UNJUDGED_LEVEL,
    C_LINKAGE_LANGUAGES,
    OverloadSelectionMixin,
    _argument_fit,
    _ArgumentTypes,
    _array_fit,
    _closest_fit,
    _could_recurse,
    _java_method_refusing,
    _kotlin_function_refusing,
    _overload_phase,
    _parameter_at,
    _parameter_taking,
    _reference_fit,
    _takes,
    _unboxed_fit,
    _untyped_agree,
)
from .call_receiver_typing import (
    _FUNCTION_KINDS,
    _SOURCE_CACHE_FILES,
    _TYPE_KINDS,
    ReceiverTypingMixin,
    _store_capped,
)
from .decorator_names import is_property_accessor
from .lang_helpers.anonymous_classes import is_anonymous_member
from .language_data import (
    get_builtin_methods,
    get_builtin_types,
    get_external_receiver_types,
    get_external_return_types,
)
from .languages.receiver_types import external_type_name, holds_function
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
from .resolvers.jvm_workspace import classify_jvm_import
from .return_types import (
    declared_return_type,
    normalize_return_type,
    signature_parameter_count,
)
from .symbol_identity import id_segment_name, outer_scope, same_parameters
from .type_names import (
    bare_type_name,
    csharp_extension_receiver,
    is_resolvable_type_name,
    strip_type_arguments,
)

log = structlog.get_logger(__name__)

# Languages with an implicit method receiver, where a bare ``foo()`` binds to
# the caller's instance. Go, Python, PHP and JS/TS spell the receiver out, so a
# bare call there is a free function and this tier would resolve it wrongly.
_IMPLICIT_RECEIVER_LANGUAGES = frozenset({"java", "csharp", "cpp", "kotlin", "apex"})

# JS/TS (and the single-file components parsed as TypeScript): a member is only
# reachable as ``this.foo()`` or ``obj.foo()``, so a bare ``foo()`` inside a
# class method never means a member of that class, whatever the file declares.
_BARE_CALL_SKIPS_MEMBERS_LANGUAGES = frozenset(
    {"javascript", "typescript", "svelte", "vue", "astro"}
)

# Languages where a call the caller's own class cannot answer is looked for on
# its ancestors, for an explicit ``self``/``this`` receiver and an implicit one.
#
# C++ is absent because its heritage binds a qualified external parent to a
# same-named local type, putting unrelated siblings in one hierarchy.
#
# C# is present: the tier answers with a name's overload set, and
# ``resolve_file`` narrows it to the member the argument count names.
_INHERITED_LANGUAGES = frozenset(
    {"java", "kotlin", "python", "typescript", "swift", "csharp", "apex"}
)

# VB.NET's own-instance receivers, lowercased: the language is case-insensitive.
_VBNET_SELF_RECEIVERS = frozenset({"me", "myclass"})

# Languages where a bare name is scoped lexically: it can only mean the
# caller's own module, an explicit ``import`` or ``open``, or the prelude, so
# repo-wide uniqueness is no evidence and only wildcard imports may merge names.
# An Apex bare name is never another class's method, however unique.
_LEXICAL_BARE_NAME_LANGUAGES = frozenset({"elixir", "fsharp", "apex"})

# Languages whose imports name what they bring into scope, so only wildcard
# imports may merge an imported file's names: ``use a::{B, C}`` binds B and C,
# never the rest of ``a``. Rust is not lexical above: its receiver-less call
# sites are mostly chained method calls, which repo-wide uniqueness still serves.
_NAMED_IMPORT_SCOPE_LANGUAGES = _LEXICAL_BARE_NAME_LANGUAGES | {"rust"}

# Languages whose module-alias binding always names a module, so a call through
# it reaches only top-level declarations. C# is absent because ``using A = N.T;``
# may alias a type, whose members it does reach. C, C++ and Ruby are absent
# because their "alias" is an included file's stem, which a call can only match
# by coinciding with a variable's name.
_MODULE_ONLY_ALIAS_LANGUAGES = frozenset(
    {"python", "typescript", "javascript", "svelte", "vue", "dart", "go", "rust", "swift"}
)

# The sentinel an import that binds a whole module's public names carries.
_WILDCARD_IMPORTED_NAMES = ["*"]


# Kinds that can never be the callee of a call, so the bare-name tiers never
# offer a data member as a function.
#
# Deliberately NOT the complement of ``_FUNCTION_KINDS``: a ``class`` is a
# constructor, a ``variable`` may be a rust tuple enum variant or a typescript
# const holding a function, and a ``type_alias`` is a Go conversion.
# ``property`` is the only kind that means "data member" and nothing else; every
# other language spells its fields ``variable``, indistinguishable by kind.
_NON_CALLABLE_KINDS = frozenset({"property"})
# Languages whose ``variable`` is always a property (`val`/`var`/`let`): a data
# member. Calling one invokes the value it holds, which is not this symbol.
_PROPERTY_VARIABLE_LANGUAGES = frozenset({"kotlin", "swift"})


# Kotlin's call operator: ``f(x)`` on a value is ``f.invoke(x)`` when its type
# declares an ``operator fun invoke``.
_INVOKE_OPERATOR = "invoke"
_OPERATOR_MODIFIER = "operator"


def _imports_a_library(imp: Import) -> bool:
    """Does *imp* name a JVM library namespace the repository does not hold?"""
    if classify_jvm_import(imp.module_path, kotlin=True) is None:
        return False
    return not imp.resolved_file or imp.resolved_file.startswith(_EXTERNAL_PREFIX)


def _is_kotlin_alias(sym: Symbol | None) -> bool:
    return sym is not None and sym.kind == "type_alias" and sym.language == "kotlin"


def _is_data_member(sym: Symbol) -> bool:
    if sym.kind in _NON_CALLABLE_KINDS:
        return True
    return sym.kind == "variable" and sym.language in _PROPERTY_VARIABLE_LANGUAGES


# A Rust ``impl`` or Swift ``extension`` block is named for the type it adds
# members to and declares nothing itself, so no bare name means it: ``Text(..)``
# constructs SwiftUI's ``Text``, not the repo's ``extension Text``. Kept out of
# the bare-name indices; its members still resolve through the method index.
_EXTENSION_KINDS = frozenset({"impl"})

# Languages admitted to the full return-type chain lane; each is admitted
# explicitly, once measured.
PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES: frozenset[str] = frozenset(
    {"apex", "cpp", "csharp", "dart", "go", "java", "kotlin", "python"}
)

# Chain languages that type the inner call by the identity of the type it
# returns (``_returned_type_id``); the rest read its type name.
_RETURNED_TYPE_ID_LANGUAGES = frozenset({"dart", "go", "kotlin", "python"})

# Chain lanes that need a file or import/re-export identity for the head type:
# a repository-global simple type name is not a language binding.
_BOUND_CHAIN_LANGUAGES = frozenset({"java", "csharp", "typescript", "go", "apex"})


def _overload_return_types(
    parsed_files: dict[str, ParsedFile],
) -> dict[tuple[str, str | None, str | None, str, int | None], set[str]]:
    """``{(path, parent, extension receiver, name, parameter count): normalised return types}``."""
    found: dict[tuple[str, str | None, str | None, str, int | None], set[str]] = defaultdict(
        set
    )
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
                symbol.extension_receiver,
                symbol.name,
                signature_parameter_count(symbol.signature or ""),
            )
            found[key].add(normalized)
    return found


def _admitted_cpp_chain(type_name: str, method_name: str) -> bool:
    return type_name == "future" and method_name == "get"


def _has_internal_linkage(path: str, sym: Symbol) -> bool:
    """Is *sym* a C/C++ free symbol only its own translation unit can name?

    The parser records ``static`` and anonymous-namespace linkage as
    ``private``. A header's copy is compiled into every file that includes it,
    so only a source file keeps the symbol to itself.
    """
    return (
        sym.language in C_LINKAGE_LANGUAGES
        and sym.parent_name is None
        and sym.visibility == "private"
        and path.lower().endswith(_SOURCE_TU_EXTS)
    )


# (parent, name) -> [(file, index id, symbol)], and [(file, symbol, index id)].
_Definitions = dict[tuple[str | None, str], list[tuple[str, str, Symbol]]]
_Declarations = list[tuple[str, Symbol, str]]


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


class CallResolver(
    CobolCallsMixin,
    LanguageStrategiesMixin,
    ReceiverTypingMixin,
    OverloadSelectionMixin,
    BarrelForwardingMixin,
    InheritanceMixin,
    CppClassScopeMixin,
):
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
        csharp_project_usings: dict[str, frozenset[str]] | None = None,
    ) -> None:
        # {type symbol id: parent type symbol ids}, from the caller's already
        # resolved heritage. Absent when the resolver is built standalone, in
        # which case the inherited tier simply never fires.
        self._heritage_parents: dict[str, set[str]] = heritage_parents or {}
        # {(file, partial type name): files declaring a fragment of it}, from
        # the builder's C#/VB.NET partial pass. Empty in every other language.
        self._partial_fragments = partial_fragments or {}
        # {C# file: the namespaces its project's global usings and MSBuild
        # ``<Using>`` items bring in}, from the builder's .NET project index.
        self._csharp_project_usings = csharp_project_usings or {}
        self._return_type_chain_languages = (
            PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
            if return_type_chain_languages is None
            else return_type_chain_languages
        )
        self._ancestors: dict[str, tuple[str, ...]] = {}
        # C++: {file: files its includes reach}, {class id: template parameter names}.
        self._cpp_include_closures: dict[str, frozenset[str]] = {}
        self._cpp_template_names: dict[str, frozenset[str]] = {}
        self._mros: dict[str, tuple[str, ...] | None] = {}
        # {type name: [(file, identity, arity)]}, filled by ``_declared_types_named``.
        self._declared_types: dict[str, list[tuple[str, tuple[str, ...], int]]] = {}
        # Per-file symbol index: {file_path: {symbol_name: symbol_id}}
        self._file_symbols: dict[str, dict[str, str]] = {}
        # The same, top-level symbols only: what a module alias can reach.
        self._file_top_level: dict[str, dict[str, str]] = {}

        # Per-file method index: {file_path: {(class_name, method_name): symbol_id}}
        self._file_methods: dict[str, dict[tuple[str, str], str]] = {}

        # Global method index: {(class_name, method_name): [(file_path, symbol_id)]}
        # in file-insertion order — replaces the trait-dispatch scan over
        # every file's method dict with one short-list lookup.
        self._global_methods: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)

        # C# extension methods and Kotlin extension functions, keyed on the
        # type they extend rather than on their holder: {(type, method):
        # (file_path, symbol_id)} and the
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

        # Representatives of sets with a member whose range is unknown; a count
        # cannot choose there, since that member may admit it too.
        self._unranged_overload_sets: set[str] = set()

        # Build variants (``f#cfg(unix)``, ``f#cfg(not(unix))``) are not chosen by
        # arguments: a call means every one, so an edge fans out to each.
        self._variant_siblings: dict[str, tuple[str, ...]] = {}
        self._symbols_by_id = {
            symbol.id: symbol for parsed in parsed_files.values() for symbol in parsed.symbols
        }
        self._symbol_paths_by_id = {
            symbol.id: path
            for path, parsed in parsed_files.items()
            for symbol in parsed.symbols
        }
        # Ids two definitions answer to; a call landing on one proves no recursion.
        self._shared_ids = _shared_definition_ids(parsed_files)
        self._overload_return_types = _overload_return_types(parsed_files)
        self._known_type_names = frozenset(
            symbol.name for symbol in self._symbols_by_id.values() if symbol.kind in _TYPE_KINDS
        )
        self._folded_types: frozenset[str] | None = None
        # {C# type name: the type symbols declaring it}, then each name's shape.
        self._csharp_type_shapes: dict[str, set[str]] | None = None
        self._csharp_shape_answers: dict[str, str | None] = {}
        self._jvm_lambda_types: dict[str, bool] | None = None
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
        self._callable_ids: set[str] = set()
        # {Kotlin file: names it imports from a library namespace}
        self._library_names: dict[str, frozenset[str]] = {}
        # The lines of the file whose declarations are being read; see ``_lines_of``.
        self._indexed_lines: tuple[str, list[str]] = ("", [])
        # C/C++ symbols with internal linkage; see ``_reachable_by_name``.
        self._tu_local_ids: set[str] = set()
        self._property_accessor_ids: set[str] = set()

        # C/C++ forward declaration → the definition it declares. Populated by
        # ``_build_indices``; ``_settle_callee`` applies it to every resolved
        # call so the edge lands on the body rather than the header line.
        self._decl_to_def: dict[str, str] = {}
        # {file: every file it includes, transitively}, filled on demand.
        self._include_closure: dict[str, set[str]] = {}

        # Plain-name alias → the real symbol it stands for (``s`` in
        # ``s = widget``), same-file only. Populated by ``_index_file``,
        # applied alongside ``_decl_to_def`` in ``_redirect_to_definition``
        # (#2791).
        self._alias_to_target: dict[str, str] = {}
        # Module-level aliases of a name the file imports (``s = attrs`` after
        # ``from ._make import attrs``), linked once barrel origins are known.
        self._imported_aliases: list[tuple[str, str, str]] = []

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
        # Each chain link's resolved inner call, for the file being resolved: a
        # self-typed hop walks back down its chain, which would otherwise
        # re-resolve every lower link once per hop above it.
        self._chain_inner_resolved: dict[tuple[Any, ...], ResolvedCall | None] = {}
        self._chain_cycle_hits = 0
        self._unindexed_extensions: frozenset[str] | None = None

        # Barrel re-export origins: {barrel_file: {name: origin_file}}
        self._barrel_origins: dict[str, dict[str, str]] = defaultdict(dict)

        # Keep reference for cross-language checks in Tier 3
        self._parsed_files = parsed_files

        self._init_workspace_indexes(repo_path)

        self._build_indices(parsed_files)
        self._follow_barrel_exports()
        self._link_imported_aliases()
        # Only a call to one of these has its named arguments typed.
        self._overloaded_names = frozenset(self._symbols_by_id[m].name for m in self._overload_rep)

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
        # (parent, name) → [(file, symbol_id, symbol)] over symbols that carry a
        # body, and the bodiless declarations waiting to be paired against them.
        # Both feed ``_link_declarations`` once every file has been indexed.
        definitions: _Definitions = defaultdict(list)
        declarations: _Declarations = []
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
        definitions: _Definitions,
        declarations: _Declarations,
        extensions: dict[tuple[str, str], set[tuple[str, str]]],
    ) -> None:
        """Index one file's symbols, collecting what is settled repo-wide later."""
        file_syms: dict[str, str] = {}
        top_level: dict[str, str] = {}
        file_methods: dict[tuple[str, str], str] = {}
        representatives = self._index_overload_sets(parsed)
        binding_counts = Counter(
            sym.name for sym in parsed.symbols if sym.parent_name is None
        )
        # (alias symbol id, the bare name it aliases) — resolved against
        # ``file_syms`` only once the whole file is indexed, so an alias
        # written above the name it aliases is not missed by source order.
        aliases: list[tuple[str, str, str | None]] = []

        for sym in parsed.symbols:
            # An overload set is indexed once, under its representative.
            index_id = representatives.get(sym.id, sym.id)
            decl_key = (sym.parent_name, sym.name)
            if sym.is_declaration:
                # A Go interface method spec is not a prototype: its body is
                # never defined, and the structural implementers that carry one
                # differ from the interface by name or package. Pairing on
                # ``(parent, name)`` would redirect the spec onto an unrelated
                # type's method, so it stays unlinked.
                if sym.language != "go":
                    declarations.append((path, sym, index_id))
            else:
                definitions[decl_key].append((path, index_id, sym))
            _index_bare_name(sym, index_id, file_syms, top_level)

            if sym.alias_of is not None and (
                binding_counts[sym.name] == 1 or "::alias@" in sym.id
            ):
                aliases.append((sym.id, sym.alias_of, sym.parent_name))

            # Method index: (class_name, method_name) → symbol_id. A property
            # holding no function is a value: ``x.f()`` never calls it, and a
            # same-named extension or inherited method is what it reaches.
            uncallable = _is_data_member(sym) and not self._holds_function(path, sym)
            if sym.parent_name and not (uncallable and sym.language in _PROPERTY_VARIABLE_LANGUAGES):
                key = (sym.parent_name, sym.name)
                file_methods[key] = index_id
                self._global_methods[key].append((path, index_id))

            extended = (
                self._csharp_extended_type(sym)
                if sym.parent_name
                else self._kotlin_extended_type(sym)
            )
            if extended is not None:
                extensions[(extended, sym.name)].add((path, index_id))

            self._index_globally(path, sym, index_id, uncallable)

        for alias_id, alias_of_name, parent_name in aliases:
            # Prefer a target in the alias's own scope before falling back
            # to a file-wide symbol with the same name.
            target_id = (
                file_methods.get((parent_name, alias_of_name))
                if parent_name is not None
                else None
            )
            if target_id is None:
                target_id = file_syms.get(alias_of_name)

            # No self-redirect: ``s = s`` names nothing beyond itself.
            if target_id is not None and target_id != alias_id:
                self._alias_to_target[alias_id] = target_id
            elif target_id is None and parent_name is None:
                self._imported_aliases.append((path, alias_id, alias_of_name))

        self._file_symbols[path] = file_syms
        self._file_top_level[path] = top_level
        self._file_methods[path] = file_methods

    def _csharp_extended_type(self, sym: Symbol) -> str | None:
        """The repository C# type *sym* extends, when it is an extension method."""
        if sym.language != "csharp":
            return None
        extended = csharp_extension_receiver(sym.signature)
        if extended is None or not is_resolvable_type_name(extended, "csharp"):
            return None
        return extended if extended in self._csharp_type_names else None

    def _kotlin_extended_type(self, sym: Symbol) -> str | None:
        """The type a top-level Kotlin extension function extends, a library one included.

        Unlike C#, a library receiver is kept: Kotlin types a ``ByteArray`` or
        ``List`` receiver too, and only an extension can be the repo's callee.
        A member extension is left out, since only its holder's scope sees it.

        Ceiling: the key is the bare type, so ``List<String>.f`` and
        ``List<Int>.f`` collide (and refuse as ambiguous), and ``Foo?`` keys as
        ``Foo``. Keying on type arguments would need the receiver's too.
        """
        if sym.language != "kotlin" or sym.kind not in _FUNCTION_KINDS:
            return None
        return self._kotlin_receiver(sym)

    def _index_globally(self, path: str, sym: Symbol, index_id: str, uncallable: bool) -> None:
        if _has_internal_linkage(path, sym):
            self._tu_local_ids.add(index_id)
        self._record_callability(index_id, uncallable)
        # A getter and its setter are two declarations under one id, which reads
        # as an overload set and is not one: the name is an attribute.
        if is_property_accessor(sym.decorators):
            self._property_accessor_ids.add(index_id)
        # Same rules as the per-file index, for the global-unique tier.
        bodiless_method = sym.is_declaration and sym.parent_name is not None
        if not (bodiless_method or sym.kind in _EXTENSION_KINDS or is_anonymous_member(sym)):
            self._global_symbols[sym.name].append(index_id)

    def _record_callability(self, index_id: str, uncallable: bool) -> None:
        """Mark *index_id* a data member's, unless anything callable shares it.

        A Kotlin `val isDark` and `fun isDark()` in one file are one id.
        """
        if uncallable:
            if index_id not in self._callable_ids:
                self._non_callable_ids.add(index_id)
        else:
            self._callable_ids.add(index_id)
            self._non_callable_ids.discard(index_id)

    def _holds_function(self, path: str, sym: Symbol) -> bool:
        """Is property *sym* declared with a function value (`val f: () -> Unit`)?"""
        if sym.kind != "variable" or sym.language not in _PROPERTY_VARIABLE_LANGUAGES:
            return False
        lines = self._lines_of(path)[sym.start_line - 1 : max(sym.end_line, sym.start_line)]
        return holds_function("\n".join(lines), sym.name, sym.language)

    def _lines_of(self, path: str) -> list[str]:
        """*path*'s lines, split once while its symbols are read one by one."""
        if self._indexed_lines[0] != path:
            self._indexed_lines = (path, self._text_of(path).splitlines())
        return self._indexed_lines[1]

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
        self, declarations: _Declarations, definitions: _Definitions
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

        C-family prototypes pair member by member (``_c_definition_of``).
        """
        redirects: dict[str, str] = {}
        for decl_file, decl, decl_index in declarations:
            rows = definitions.get((decl.parent_name, decl.name), ())
            if decl.language in C_LINKAGE_LANGUAGES:
                target = self._c_definition_of(decl_file, decl, decl_index, rows)
                if target is not None:
                    redirects[decl.id] = target
                continue
            candidates = [(def_file, sym_id) for def_file, sym_id, _sym in rows]
            # An overload stub's implementation shares its id: already defined.
            if any(sym_id == decl_index for _f, sym_id in candidates):
                continue
            defined = self._defining_set(decl_file, decl.parent_name, candidates)
            if defined is not None:
                redirects[decl_index] = defined
        return redirects

    def _defining_set(
        self, decl_file: str, parent: str | None, candidates: list[tuple[str, str]]
    ) -> str | None:
        """The one definition a declaration in *decl_file* can mean, or None."""
        # Deduped by symbol id, not by row: an overload set defined in one
        # file is several definitions sharing one id, and counting rows
        # reads that as an ambiguity that does not exist.
        including = {
            sym_id
            for def_file, sym_id in candidates
            if decl_file in self._import_targets.get(def_file, ())
        }
        if len(including) == 1:
            return next(iter(including))
        distinct = {sym_id for _def_file, sym_id in candidates}
        if len(distinct) == 1 and (
            parent is None or _same_translation_unit(decl_file, candidates[0][0])
        ):
            return next(iter(distinct))
        return None

    def _c_definition_of(
        self, decl_file: str, decl: Symbol, decl_index: str, rows: list[tuple[str, str, Symbol]]
    ) -> str | None:
        """The definition C-family prototype *decl* declares, member by member.

        Another member of *decl*'s own overload set is a different function,
        and a ``static`` body in another translation unit is invisible to the
        header, so neither is a candidate. Definitions with *decl*'s parameter
        list in its own source file or in a file including it, directly or
        not, come first when there are any: a file including the header
        directly may define a same-named function of another namespace.
        Platform variants defining one signature in two files stay unlinked.
        """
        # A same-file definition shares the prototype's id: already defined.
        if any(sym.id == decl.id for _f, _i, sym in rows):
            return None
        rows = [
            (def_file, index_id, sym)
            for def_file, index_id, sym in rows
            if index_id != decl_index
            and (def_file == decl_file or index_id not in self._tu_local_ids)
        ]
        candidates = self._signature_candidates(decl_file, decl, rows)
        defined = self._defining_set(decl_file, decl.parent_name, candidates)
        if defined is None:
            return None
        return self._paired_member(decl, decl_index, [r[2] for r in rows if r[1] == defined])

    def _signature_candidates(
        self, decl_file: str, decl: Symbol, rows: list[tuple[str, str, Symbol]]
    ) -> list[tuple[str, str]]:
        """*rows* narrowed to bodies with *decl*'s parameters that see it, when any.

        Only asked when several definitions compete, which keeps the include
        walk and the signature reads off the common one-definition path.
        """
        everything = [(def_file, index_id) for def_file, index_id, _sym in rows]
        if len({index_id for _f, index_id in everything}) < 2:
            return everything
        matching = [
            (def_file, index_id)
            for def_file, index_id, sym in rows
            if same_parameters(decl, sym)
            and (
                _same_translation_unit(decl_file, def_file)
                or self._includes(def_file, decl_file)
            )
        ]
        return matching or everything

    def _includes(self, path: str, header: str) -> bool:
        """Does *path* include *header*, directly or through other headers?"""
        reached = self._include_closure.get(path)
        if reached is None:
            reached = self._include_closure[path] = set()
            stack = [path]
            while stack:
                fresh = set(self._import_targets.get(stack.pop(), ())) - reached
                reached |= fresh
                stack.extend(fresh)
        return header in reached

    @property
    def declaration_definitions(self) -> dict[str, str]:
        """``{declaration symbol id: definition symbol id}`` for paired decls.

        Read by the graph builder, which stamps the pairing on the declaration
        node so the dead-code pass can tell a superseded declaration from an
        orphaned prototype whose definition no longer exists.
        """
        return self._decl_to_def

    def _redirect_to_definition(self, resolved: ResolvedCall) -> ResolvedCall | None:
        """Redirect calls through aliases and declarations to the final target.

        None when the target is the caller itself: recursion through its own
        prototype draws no edge, as recursion on an overload does not.
        """
        original = resolved.callee_id
        target = original
        visited: set[str] = set()

        while target not in visited:
            visited.add(target)
            next_target = self._decl_to_def.get(target)
            if next_target is None:
                next_target = self._alias_to_target.get(target)
            if next_target is None:
                break
            target = next_target

        # A cycle makes the redirect ambiguous; keep the original resolution.
        if target in visited and (
            self._decl_to_def.get(target) is not None
            or self._alias_to_target.get(target) is not None
        ):
            return resolved

        if target == original:
            return resolved
        if target == resolved.caller_id:
            return None if original in self._decl_to_def else resolved

        return ResolvedCall(
            resolved.caller_id,
            target,
            resolved.confidence,
            resolved.line,
            resolved.origin,
        )

    def _published(self, file_path: str, name: str, *, top_level: bool = False) -> str | None:
        """The symbol *file_path* publishes under *name*, or None.

        A module may declare a symbol under one name and export it under
        another — ``export { stringType as string }`` — and every lookup that
        arrives through a namespace, a barrel or an import asks for the
        published name while the symbol table holds the local one. The table
        answers first, so the alias can only ever add a hit. *top_level*
        leaves class members out of the lookup.
        """
        table = self._file_top_level if top_level else self._file_symbols
        symbols = table.get(file_path, {})
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
        self._chain_inner_resolved.clear()

        for call in calls:
            if not call.caller_symbol_id:
                # Module-level call — assign to synthetic __module__ symbol
                call = replace(call, caller_symbol_id=f"{file_path}::__module__")
            call = self._typed_arguments(file_path, call)

            resolved = self._overload_sibling_call(call) or self._resolve_one(file_path, call)
            if resolved:
                resolved = self._settle_callee(resolved, call)
            if resolved:
                # The edge type is a property of the call syntax, not of the
                # tier that answered, so it is stamped once here.
                if call.edge_type != "calls":
                    resolved = replace(resolved, edge_type=call.edge_type)

                if call.spawned:
                    resolved = replace(resolved, spawned=True)

                results.extend(
                    replace(resolved, callee_id=variant)
                    for variant in self._build_variants(resolved)
                )

        return results

    def _settle_callee(self, resolved: ResolvedCall, call: CallSite) -> ResolvedCall | None:
        """Carry *resolved* through prototypes, aliases and overload sets to its body.

        A C-family prototype pairs member by member, so its overload set is
        narrowed before the redirect; any other set is redirected first and
        narrowed among its definition's members. The final redirect covers a
        chosen member whose body lives in another file.
        """
        callee = self._symbols_by_id.get(resolved.callee_id)
        member_paired = (
            callee is not None
            and callee.language in C_LINKAGE_LANGUAGES
            and resolved.callee_id in self._overload_rep
        )
        if not member_paired:
            resolved = self._redirect_to_definition(resolved)
        if resolved:
            resolved = self._narrow_overload(resolved, call)
        return self._redirect_to_definition(resolved) if resolved else None

    def _resolve_one(self, file_path: str, call: CallSite) -> ResolvedCall | None:
        """Resolve a single CallSite through the three-tier fallback."""
        caller_id = call.caller_symbol_id
        assert caller_id is not None

        language = self._language_of(file_path) or ""
        if language == "cobol":
            return self._resolve_cobol_call(file_path, call, caller_id)
        receiver_call = call.receiver_call
        if _is_super_call(call, language):
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

        strategies = self._strategies_for(file_path)
        bare_fallback = strategies.member_bare_fallback
        # --- Method call with receiver: receiver.method() ---
        if call.receiver_name:
            handled, hit = self._resolve_member_call(file_path, call, caller_id)
            if not handled and call.bare_name_fallback and bare_fallback:
                hit = self._resolve_free_call(file_path, call, caller_id)
            return self._with_props(hit, call)
        if receiver_call is not None and not (bare_fallback and strategies.chain_bare_fallback):
            return None
        if call.on_unnamed_object:
            # A call on an object no pattern names: a tier keyed on the
            # caller's file, class or imports would read the method as theirs.
            return self._with_props(self._global_unique_call(file_path, call, caller_id), call)

        # --- Free function call: function() ---
        return self._with_props(self._resolve_bare_call(file_path, call, caller_id), call)

    def _through_kotlin_alias(
        self, alias: Symbol, call: CallSite, caller_id: str
    ) -> ResolvedCall | None:
        """The repo class a Kotlin ``typealias`` names, as a call in its file would reach it."""
        _, _, target = (alias.signature or "").partition(" = ")
        head = strip_type_arguments(target).rstrip("? ").strip()
        alias_file = self._symbol_paths_by_id.get(alias.id)
        # A function type, a qualified (possibly external) name or the alias's own
        # name gives no repo class to construct.
        if alias_file is None or not head.isidentifier() or head == alias.name:
            return None
        hit = self._resolve_free_call(alias_file, replace(call, target_name=head), alias.id)
        target_sym = self._symbols_by_id.get(hit.callee_id) if hit is not None else None
        if target_sym is None or target_sym.kind != "class" or target_sym.name == alias.name:
            return None
        return replace(hit, caller_id=caller_id)

    def _resolve_bare_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """A receiver-less call, refused where it lands on an extension it has no receiver for.

        A Kotlin ``typealias`` is followed to the class it names, resolved from
        the alias's own file, and refused when that is not a repo class:
        ``IOException(..)`` through ``actual typealias IOException =
        java.io.IOException`` constructs the external type, which the alias is
        not. Ceiling: a qualified target (``= app.data.Repo``) is refused too.
        """
        invoked = self._operator_invoke_call(file_path, call, caller_id)
        if invoked is not None:
            return invoked
        hit = self._resolve_free_call(file_path, call, caller_id)
        if hit is not None and self._cpp_names_no_repo_member(file_path, call, hit.callee_id):
            return None
        alias = self._symbols_by_id.get(hit.callee_id) if hit is not None else None
        if _is_kotlin_alias(alias):
            return self._through_kotlin_alias(alias, call, caller_id)
        if hit is None or call.receiver_call is not None:
            return hit
        return None if self._lacks_extension_receiver(file_path, call, hit.callee_id) else hit

    def _operator_invoke_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``useCase(args)`` on a Kotlin value whose type declares ``operator fun invoke``.

        The name is typed as the receiver of ``useCase.invoke(args)``: a local,
        a parameter, a property of the caller's class or of its file. A local or
        parameter outranks every function of the name. A property does not
        outrank a function its own scope declares (Kotlin prefers the function
        at one level), so a member or top-level ``fun`` of the name leaves the
        call to the function tiers. The operator may be inherited. A name no
        declaration types, or a type with no such operator, falls through to
        the function tiers too.
        """
        if not self._may_invoke(file_path, call):
            return None
        typed = self._invoked_value_type(file_path, call, caller_id)
        if typed is None:
            return None
        type_name, scope, typing_file = typed
        as_member = replace(call, receiver_name=call.target_name, target_name=_INVOKE_OPERATOR)
        if "::" in type_name:
            hit = self._call_typed_receiver(file_path, as_member, caller_id, type_name)
            return hit if hit is not None and self._is_operator_invoke(hit.callee_id) else None
        found = self._invoke_target(typing_file, as_member, caller_id, type_name)
        if found is None:
            return None
        typed_call = self._field_typed_call if scope == "field" else self._body_typed_call
        return typed_call(caller_id, found[0], found[1], call.line)

    def _may_invoke(self, file_path: str, call: CallSite) -> bool:
        """Is *call* a bare Kotlin call in a repo that declares some ``invoke`` method?"""
        return (
            call.receiver_call is None
            and self._language_of(file_path) == "kotlin"
            and _INVOKE_OPERATOR in self._method_names()
        )

    def _invoke_target(
        self, typing_file: str, as_member: CallSite, caller_id: str, type_name: str
    ) -> tuple[str, str] | None:
        """``(operator invoke id, tier)`` *type_name* declares or inherits, if any."""
        found = self._typed_receiver_target(
            typing_file, as_member, caller_id, type_name
        ) or self._inherited_typed_target(
            typing_file, as_member, caller_id, id_segment_name(type_name)
        )
        return found if found is not None and self._is_operator_invoke(found[0]) else None

    def _invoked_value_type(
        self, file_path: str, call: CallSite, caller_id: str
    ) -> tuple[str, str, str] | None:
        """``(type, scope, typing file)`` of the value a bare Kotlin call names, if it may invoke.

        None for an untyped or builtin-typed name, and for a property (not a
        local or parameter) whose scope also declares a function of the name.
        """
        name = call.target_name
        type_name, scope, typing_file = self._receiver_type_and_scope(
            file_path, caller_id, "kotlin", name
        )
        if type_name is None:
            type_name, scope = self._module_types_in(file_path, "kotlin").get(name), "module"
        if type_name is None or external_type_name(type_name) is not None:
            return None
        if scope != "body" and self._function_in_scope(file_path, call, caller_id):
            return None
        return type_name, scope, typing_file

    def _is_operator_invoke(self, sym_id: str) -> bool:
        symbol = self._symbols_by_id.get(sym_id)
        return (
            symbol is not None
            and symbol.name == _INVOKE_OPERATOR
            and _OPERATOR_MODIFIER in symbol.modifiers
        )

    def _function_in_scope(self, file_path: str, call: CallSite, caller_id: str) -> bool:
        """Does the caller's class, or its file at top level, declare a function *call* names?

        A Kotlin ``val`` and ``fun`` of one name share an id; the id is callable
        when the function is there.
        """
        own = self._enclosing_class_method(file_path, call, caller_id)
        top = self._file_top_level.get(file_path, {}).get(call.target_name)
        return any(sym_id in self._callable_ids for sym_id in (own, top) if sym_id is not None)

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
        if language in _RETURNED_TYPE_ID_LANGUAGES and inner_callee is not None:
            # A go method may sit in any file of its type's package, so the
            # type is read by identity and the method asked for across it.
            # Python and Dart read the return type in the callee's file the same way.
            type_id = self._returned_type_id(inner_callee)
            hit = (
                None
                if type_id is None
                else self._call_typed_receiver(file_path, call, caller_id, type_id)
            )
            if hit is not None:
                tier = hit.origin.removeprefix("receiver_typed_")
                return True, self._return_typed_call(caller_id, hit.callee_id, tier, call.line)
            if language == "kotlin" and type_id is not None:
                # A Kotlin extension function can add the method to the type the
                # inner call returns; a member of the type still wins over it.
                _, extension = self._extension_call(
                    file_path, call, caller_id, self._symbols_by_id[type_id].name
                )
                if extension is not None:
                    return True, extension
        if language in ("python", "dart", "kotlin"):
            # A type name read in the caller's file is not evidence, and an
            # inherited method or an extension is not a refusal: the bare name
            # keeps the site where the language allows one.
            return False, None
        type_name = (
            tabled
            if tabled is not None
            else self._callee_chain_return_type(file_path, call, inner, inner_callee, caller_id, language)
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
        type_id = self._unique_type_id(type_name, "csharp")
        if type_id is not None:
            inherited = self._ancestor_method(type_id, call.target_name, caller_id)
            if inherited is not None:
                same_file = self._symbol_paths_by_id.get(inherited) == file_path
                tier = "same_file" if same_file else "import"
                return True, self._return_typed_call(caller_id, inherited, tier, call.line)

        answered, hit = self._extension_call(file_path, call, caller_id, type_name)
        if answered:
            return True, hit
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

    def _unique_type_id(self, type_name: str, language: str) -> str | None:
        """The one *language* type *type_name* (``IFoo`1`` or ``IFoo``) names repo-wide, or None."""
        bare, _, arity = type_name.partition("`")
        shape = (language, int(arity or 0))
        candidates = [
            symbol
            for sym_id in self._global_symbols.get(bare, ())
            if (symbol := self._symbols_by_id[sym_id]).kind in _TYPE_KINDS
            and (symbol.language, symbol.type_parameter_count or 0) == shape
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
        memo_key = (*link, inner.argument_count, caller_id)
        if memo_key in self._chain_inner_resolved:
            resolved_inner = self._chain_inner_resolved[memo_key]
        elif link in self._chain_links_in_flight:
            self._chain_cycle_hits += 1
            return None
        else:
            resolved_inner = self._resolve_chain_link(file_path, call, inner, caller_id, language)
        if resolved_inner is None:
            return None
        # The overload the inner call's arguments select is the one whose type it returns.
        callee_id = resolved_inner.callee_id
        rep = self._overload_rep.get(callee_id)
        if rep is not None:
            tied = self._tied_without_representative(
                rep, self._deciding_overloads(rep, inner.argument_count)
            )
            # A tie names a type only when every tied member returns it.
            returns = {
                self._callee_return_type(member, inner.argument_count, language) for member in tied
            }
            if len(returns) > 1 or None in returns:
                return None
            target = self._overload_target(rep, inner.argument_count)
            if target != rep:
                callee_id = target
        return callee_id

    def _resolve_chain_link(
        self,
        file_path: str,
        call: CallSite,
        inner: CallReceiver,
        caller_id: str,
        language: str,
    ) -> ResolvedCall | None:
        """Resolve a chain's inner call, remembered unless a cycle cut it short."""
        link = (file_path, call.line, inner.receiver_name, inner.target_name)
        inner_call = CallSite(
            target_name=inner.target_name,
            receiver_name=inner.receiver_name,
            caller_symbol_id=caller_id,
            line=call.line,
            argument_count=inner.argument_count,
            receiver_call=self._nested_receiver_call(file_path, call.line, inner, language),
        )
        cycles = self._chain_cycle_hits
        self._chain_links_in_flight.add(link)
        try:
            resolved = self._resolve_one(file_path, inner_call)
        finally:
            self._chain_links_in_flight.discard(link)
        if self._chain_cycle_hits == cycles:
            self._chain_inner_resolved[(*link, inner.argument_count, caller_id)] = resolved
        return resolved

    def _callee_chain_return_type(
        self,
        file_path: str,
        call: CallSite,
        inner: CallReceiver,
        callee_id: str | None,
        caller_id: str,
        language: str,
    ) -> str | None:
        if callee_id is None:
            return None
        if language == "java" and self._returns_java_self_type(callee_id):
            # A self-bounded ``B`` is the type of whatever the call was made on.
            # Ceiling: only a chained receiver is typed; ``b.name(x).w(1)`` stops.
            nested = self._nested_receiver_call(file_path, call.line, inner, language)
            if nested is None:
                return None
            nested_callee = self._chain_inner_callee(file_path, call, nested, caller_id, language)
            return self._callee_chain_return_type(
                file_path, call, nested, nested_callee, caller_id, language
            )
        return self._callee_return_type(callee_id, inner.argument_count, language)

    def _nested_receiver_call(
        self, file_path: str, line: int, inner: CallReceiver, language: str
    ) -> CallReceiver | None:
        """The call *inner* is itself chained onto: ``a()`` under ``a().b().c()``.

        A receiver records one hop, but the parser keeps every link of a chain
        as its own site on the chain's first line, so the hop below is read
        off the site *inner* names. Apex, C#, Dart, Go, Java and Kotlin read it, so a
        builder chain types each link from the one before; elsewhere an inner call
        is still typed as if nothing were chained under it. A name repeated in
        one chain (``a().b().b()``) keeps its first link, the innermost.
        """
        if language not in ("apex", "csharp", "dart", "go", "java", "kotlin"):
            return None
        sites = self._chained_sites.get(file_path)
        if sites is None:
            parsed = self._parsed_files.get(file_path)
            sites = {}
            for c in parsed.calls if parsed else ():
                if c.receiver_call is not None:
                    sites.setdefault((c.line, c.receiver_name, c.target_name), c.receiver_call)
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
        if self._overloads_disagree(symbol, symbol_path, argument_count):
            return None
        if type_name is not None and language == "java":
            # Read where the method declares it, with its member types in scope.
            return self._read_java_type(symbol_path, callee_id, type_name)
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
        An anonymous class or enum-constant body (``Gson::FILTER::Filter``)
        sees its own members first, then those of the class around it.
        """
        parsed = self._parsed_files.get(file_path)
        if parsed is None or parsed.file_info.language not in _IMPLICIT_RECEIVER_LANGUAGES:
            return None
        if call.receiver_name:
            return None
        methods = self._file_methods.get(file_path, {})
        caller_class = _extract_class_from_symbol_id(caller_id)
        while caller_class:
            own = methods.get((caller_class, call.target_name))
            if own is not None:
                return own
            caller_class = outer_scope(caller_class)
        return None

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
        # A C++ class member hides a same-named function, so the caller's class
        # and its bases answer ahead of the flat same-file table. A qualified
        # ``ns::f()`` names its scope, not the caller's class.
        if not call.receiver_name and not call.scope_name:
            own = self._cpp_class_member_call(file_path, call, caller_id, bare=True)
            if own is not None:
                return own

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

        # In a Kotlin extension a bare name is a member of the extended type
        # first: it beats every package, import and repo-wide match below.
        if declared and not call.receiver_name:
            hit = self._extension_receiver_member_call(file_path, call, caller_id)
            if hit is not None:
                return hit

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
        # A Kotlin import from a library namespace the repo does not hold
        # (``import java.io.IOException``) binds the name just as surely.
        if not declared or target_name in self._library_imported_names(file_path):
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
        own = self._enclosing_class_method(file_path, call, caller_id)
        # Own scope answers recursion, where the site proves it is recursion.
        own_refused = own == caller_id and not self._is_bare_recursion(file_path, call, caller_id)
        if self._is_anonymous_id(own):
            return True, None if own_refused else _own_member_call(caller_id, own, call)
        # ``_file_symbols`` is flat and holds class members, but a JS/TS bare
        # call can only name a top-level binding: a same-named method must not
        # shadow an import (or hide a top-level function declared before it).
        table = (
            self._file_top_level
            if self._language_of(file_path) in _BARE_CALL_SKIPS_MEMBERS_LANGUAGES
            else self._file_symbols
        )
        callee_id = table.get(file_path, {}).get(call.target_name)
        if callee_id is None:
            return False, None
        if own is not None and own != callee_id and _rivals_a_class_method(callee_id):
            # Own class first: the flat hit is a stranger.
            if own_refused:
                return True, None
            return True, ResolvedCall(caller_id, own, 0.95, call.line, "enclosing_class")
        if callee_id != caller_id or self._bare_name_reaches_caller(file_path, call, own):
            return True, ResolvedCall(caller_id, callee_id, 0.95, call.line, "same_file")
        # A method no bare name reaches here (Python, Go, Rust) is not this call's target.
        return False, None

    def _bare_name_reaches_caller(self, file_path: str, call: CallSite, own: str | None) -> bool:
        """Is a bare call the flat index answers with its own caller recursion?

        From the caller's own class scope, or when the caller is a top-level
        function. Answered here, at the caller's own scope, so recursion never
        falls through to an import, an ancestor or a repo-wide match.
        """
        caller_id = call.caller_symbol_id or ""
        in_scope = own == caller_id or self._symbols_by_id[caller_id].parent_name is None
        return in_scope and self._is_bare_recursion(file_path, call, caller_id)

    def _is_bare_recursion(self, file_path: str, call: CallSite, caller_id: str) -> bool:
        """Is a receiver-less call to the caller's own name recursion?

        Only where the site is written bare: the parser can lose a receiver or
        qualifier (``Outer.this.f()``, ``::f()``, ``a().f()``) that the source
        line still shows. Not in Kotlin, where a lambda with a receiver
        (``apply { f() }``) rebinds a bare name, nor past a Python import of the
        name inside the caller. Ceiling: a bare site on a line that also
        writes the name qualified is taken as written bare.
        """
        if self._refuses_recursion(caller_id, caller_id, call):
            return False
        if call.receiver_name or call.receiver_call is not None or call.scope_name:
            return False
        language = self._language_of(file_path) or ""
        caller = self._symbols_by_id[caller_id]
        lines = self._lines_of(file_path)
        if self._language_rebinds(language, lines, caller, call):
            return False
        return _written_bare(lines, caller, call)

    def _language_rebinds(
        self, language: str, lines: list[str], caller: Symbol, call: CallSite
    ) -> bool:
        """Does *language* let the caller's own bare name mean something else here?

        A Kotlin lambda with a receiver, a Python import inside the caller, a
        C++ free function of the name in another namespace, a C# explicit
        interface member (``IValidator.Validate(..)``), which has no bare name.
        """
        if language == "kotlin":
            return True
        if language == "python":
            return _imports_name(lines, caller, call.target_name)
        if language == "cpp":
            return self._namespace_rival(caller, call)
        if language == "csharp":
            return _declared_explicitly(lines, caller, call.target_name)
        return False

    def _site_shows_recursion(self, call: CallSite) -> bool:
        """Does a call whose arguments pick its own caller read as a call to itself?

        Not on a chained receiver, and a receiver-less site only when written bare.
        """
        if call.receiver_call is not None:
            return False
        if call.receiver_name is not None:
            return True
        caller_id = call.caller_symbol_id or ""
        file_path = self._symbol_paths_by_id.get(caller_id, "")
        return self._is_bare_recursion(file_path, call, caller_id)

    def _namespace_rival(self, caller: Symbol, call: CallSite) -> bool:
        """Does another C++ free function of the caller's name take the call's arguments?

        Namespaces are on no symbol, so ``detail::f`` and ``fmt::f`` look alike
        and argument lookup may pick either: the call is no proof of recursion.
        """
        if caller.parent_name is not None:
            return False
        return any(
            self._decl_to_def.get(sym_id, sym_id) != caller.id
            and _free_function_taking(self._symbols_by_id.get(sym_id), call)
            for sym_id in self._global_symbols.get(call.target_name, ())
        )

    def _is_anonymous_id(self, sym_id: str | None) -> bool:
        """Does *sym_id* name an anonymous class member?"""
        return sym_id is not None and is_anonymous_member(self._symbols_by_id[sym_id])

    def _bound_import_call(
        self,
        call: CallSite,
        caller_id: str,
        binding: NamedBinding | None,
    ) -> ResolvedCall | None:
        """2a: the specific imported name, binding-aware."""
        published = self._imported_symbol(binding, call.target_name)
        if published is None:
            return None
        return ResolvedCall(caller_id, published, 0.90, call.line, "import_scoped")

    def _imported_symbol(self, binding: NamedBinding | None, name: str) -> str | None:
        """The repo symbol an imported *name* binds, one barrel hop followed."""
        if not (binding and binding.source_file):
            return None
        source_file = binding.source_file
        barrel = self._barrel_origins.get(source_file, {})
        lookup_name = binding.exported_name or name
        if lookup_name in barrel:
            source_file = barrel[lookup_name]
        return self._published(source_file, lookup_name)

    def _link_imported_aliases(self) -> None:
        """Redirect a module-level alias of an imported name onto its definition.

        ``s = attributes = attrs`` after ``from ._make import attrs`` stands
        for ``_make.attrs``; the same-file pass in ``_index_file`` finds no
        ``attrs`` to link. An ``import x`` binding names a module, so it is
        skipped. Ceiling: ``from pkg import sub`` binds a submodule like a name
        and resolves as the import tier does; calling a module is never valid.
        """
        for path, alias_id, name in self._imported_aliases:
            binding = self._import_bindings.get(path, {}).get(name)
            if binding is None or binding.is_module_alias:
                continue
            target_id = self._imported_symbol(binding, name)
            if target_id is not None and target_id != alias_id:
                self._alias_to_target[alias_id] = target_id

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

    def _library_imported_names(self, file_path: str) -> frozenset[str]:
        """The names a Kotlin file binds by importing a JVM library namespace.

        A namespace ``classify_jvm_import`` knows (the JDK, the Kotlin stdlib,
        ``kotlinx``, ``androidx``) counts unless the import resolved into the
        repo, which may be that library. An aliased import binds the alias only.
        """
        names = self._library_names.get(file_path)
        if names is not None:
            return names
        parsed = self._parsed_files.get(file_path)
        kotlin = parsed is not None and parsed.file_info.language == "kotlin"
        imports = parsed.imports if kotlin else ()
        names = frozenset(
            name
            for imp in imports
            if _imports_a_library(imp)
            for name in imp.local_names or [bare_type_name(imp.module_path)]
        ) - {"*"}
        self._library_names[file_path] = names
        return names

    def _repo_wide_free_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Tier 3 and what follows it: answers not grounded in this file or its imports."""
        # Ahead of tier 3: the caller's own hierarchy is better evidence than
        # repo-wide uniqueness, and a JVM package sibling's member never answers.
        inherited = self._implicit_inherited_call(file_path, call, caller_id)
        if inherited is not None:
            return inherited
        hit = self._first_strategy_hit(
            self._strategies_for(file_path).free_fallback, file_path, call, caller_id
        )
        if hit is not None:
            return hit
        return self._global_unique_call(file_path, call, caller_id)

    def _global_unique_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Tier 3: the one declaration of the name repo-wide, only within the same language."""
        target_name = call.target_name
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

        # An overload set is several declarations under one id, not an ambiguity.
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
        the includer's translation unit. Nor across independent builds
        (``_in_reach_of_build``).
        """
        path = self._symbol_paths_by_id.get(sym_id, "")
        if sym_id in self._tu_local_ids and path != file_path:
            return False
        return self._in_reach_of_build(file_path, path)

    def _implicit_inherited_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """A bare call an ancestor of the caller's class answers.

        A member call on its bare fallback is refused, as in
        ``_enclosing_class_method``.
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

        hit = self._first_strategy_hit(
            self._strategies_for(file_path).constructor, file_path, call, caller_id
        )
        if hit is not None:
            return True, hit

        # Every strategy below ends in a lookup keyed on the method name, so a
        # name the repo declares nowhere cannot resolve.
        if method_name not in self._global_symbols and not self._bodiless_callee_named(
            file_path, method_name
        ):
            return False, None

        # A Go local, parameter or ``var`` the body declares is read through its
        # declared type, never as a type name: ``reader.Peek()`` on a
        # ``reader text.Reader`` is the interface's method, not the unexported
        # struct ``reader``'s. Go has no method call on a type name outside a
        # method expression, so the variable reading is the right one.
        if self._go_receiver_is_variable(file_path, call, caller_id):
            hit = self._first_strategy_hit(
                self._strategies_for(file_path).member, file_path, call, caller_id
            )
            if hit is not None:
                return True, hit
            hit = self._unclassed_receiver_call(file_path, call, caller_id)
            if hit is None and self._receiver_type_is_external(file_path, call, caller_id):
                return True, None
            return hit is not None, hit

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

    def _bodiless_callee_named(self, file_path: str, method_name: str) -> bool:
        """Whether a bodiless method ``_global_symbols`` leaves out can be the callee.

        A Go interface method spec and a C++ pure virtual are what an
        interface-typed receiver binds to. A C++ declaration nothing defines
        is not, so only pure virtual names are admitted there.
        """
        language = self._language_of(file_path)
        if language == "go":
            return method_name in self._method_names()
        return language == "cpp" and method_name in self._pure_virtual_names()

    def _go_receiver_is_variable(self, file_path: str, call: CallSite, caller_id: str) -> bool:
        """Whether a Go call's receiver is a typed local, parameter or ``var`` of the caller."""
        receiver_name = call.receiver_name or ""
        if "." in receiver_name or self._language_of(file_path) != "go":
            return False
        return receiver_name in self._declared_types_in(file_path, caller_id, "go")

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
            return self._module_alias_call(file_path, module_file, call, caller_id)

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
        file_path: str,
        module_file: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        method_name = call.target_name
        # Where the alias can only name a module, ``log.message()`` reaches the
        # module's own ``message``, never a same-named method of a class in it.
        top_level = self._language_of(file_path) in _MODULE_ONLY_ALIAS_LANGUAGES
        if top_level:
            published = self._published(module_file, method_name, top_level=True)
        else:
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
        published = self._published(origin, declared_name, top_level=top_level)
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
        if sym_id is None or self._refuses_recursion(sym_id, caller_id, call):
            return None
        # The caller itself is recursion, answered here so it never reaches an ancestor.
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
        if self._language_of(file_path) == "cpp":
            if call.receiver_name != "this":
                return None
            return self._cpp_class_member_call(file_path, call, caller_id, bare=False)
        if self._language_of(file_path) not in _INHERITED_LANGUAGES:
            return None
        sym_id = self._inherited_method(caller_id, call.target_name)
        if sym_id is None:
            return None
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "self_inherited")

    def _language_of(self, file_path: str) -> str | None:
        parsed = self._parsed_files.get(file_path)
        return parsed.file_info.language if parsed else None

    def _receiver_pair_match(
        self,
        file_path: str,
        key: tuple[str, str],
        spelled: str | None = None,
    ) -> tuple[str, str] | None:
        """The symbol a ``(class, method)`` pair names, and the scope that held it.

        No class declares the pair unless the global method index holds it, and
        that check also keeps the merged-import view from being built for a
        pair that cannot be in it. *spelled* is the type as the call site
        wrote it when that carries a C# arity (``Policy`1``) the key drops.
        """
        if key not in self._global_methods:
            return None

        file_methods = self._file_methods.get(file_path, {})
        if key in file_methods:
            return file_methods[key], "same_file"

        merged_methods = self._merged_methods_for(file_path)
        if key in merged_methods:
            return merged_methods[key], "import"

        # An Apex type is one its project reaches (a strategy answers that), never repo-wide.
        if self._language_of(file_path) == "apex":
            return None
        return self._repo_wide_method(key, spelled or key[0])

    def _repo_wide_method(self, key: tuple[str, str], spelled: str) -> tuple[str, str] | None:
        """The global tier: the one method *key* can name anywhere, or None.

        Nothing at this scope tells two same-named types apart, so it answers
        only when one repository type can be meant and one method of it
        matches. Otherwise a call on one project's ``MainWindow`` binds to
        whichever ``MainWindow`` sorts first, even one that merely inherits
        the method. A file declaring only a type the call cannot mean (another
        arity of a C# generic) holds no candidate; any other file, including
        one with no type of that name (a trait ``impl``, an out-of-line C++
        body), still does.
        """
        bare, _, arity = spelled.partition("`")
        declared = self._declared_types_named(bare)
        meant = {identity for _, identity, count in declared if count == int(arity or 0)}
        if len(meant) > 1:
            return None
        owners: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        for path, identity, _ in declared:
            owners[path].add(identity)
        candidates = self._collapse_declarations(
            [
                sym_id
                for path, sym_id in self._global_methods[key]
                if len(owners.get(path, ())) != 1 or owners[path] <= meant
            ]
        )
        if len(candidates) != 1:
            return None
        return next(iter(candidates)), "global"

    def _declared_types_named(self, name: str) -> list[tuple[str, tuple[str, ...], int]]:
        """``(file, identity, arity)`` for each repository type declared as *name*.

        The fragments of one ``partial`` type share an identity. A forward
        declaration and a Rust ``impl`` block declare no type.
        """
        found = self._declared_types.get(name)
        if found is not None:
            return found
        found = []
        for sym_id in self._global_symbols.get(name, ()):
            symbol = self._symbols_by_id[sym_id]
            if symbol.kind not in _TYPE_KINDS or symbol.kind == "impl" or symbol.is_declaration:
                continue
            path = self._symbol_paths_by_id[sym_id]
            arity = symbol.type_parameter_count or 0
            identity = self._partial_fragments.get((path, name)) or (sym_id,)
            found.append((path, (*identity, str(arity)), arity))
        self._declared_types[name] = found
        return found

    def _answers_for_a_foreign_type(self, file_path: str, receiver_name: str) -> bool:
        """Is the repo-wide tier about to answer a call on a type we do not own?

        Asked only of the ``global`` tier, which answers whenever the
        repository declares one type of that name with the method. A
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


def _index_bare_name(
    sym: Symbol, index_id: str, file_syms: dict[str, str], top_level: dict[str, str]
) -> None:
    """Index *sym* under its bare name in one file's flat and top-level tables."""
    if sym.is_declaration:
        # A declaration must never displace a definition already
        # indexed under this name — a .cpp that forward-declares a
        # helper above its own body holds both.
        #
        # A method declaration stays out: this index answers
        # unqualified lookups from importing files, and no bare name
        # can legally reach a method. The (class, method) index
        # still takes it.
        if sym.parent_name is None:
            file_syms.setdefault(sym.name, index_id)
            top_level.setdefault(sym.name, index_id)
        return
    if sym.kind in _EXTENSION_KINDS:
        return
    # Top-level symbols and methods. An anonymous class member stays out: only
    # its own class body names it bare, through the (class, method) index. It
    # is never top-level, so ``top_level`` needs no such check.
    if not is_anonymous_member(sym):
        file_syms[sym.name] = index_id
    if sym.parent_name is None:
        top_level[sym.name] = index_id


def _free_function_taking(symbol: Symbol | None, call: CallSite) -> bool:
    """Is *symbol* a free function that can take *call*'s arguments?"""
    if symbol is None or symbol.kind not in _FUNCTION_KINDS or symbol.parent_name is not None:
        return False
    return _could_recurse(symbol, call.argument_count)


def _shared_definition_ids(parsed_files: dict[str, ParsedFile]) -> frozenset[str]:
    """Ids more than one definition answers to (twins, generic or trait overloads)."""
    defined = Counter(
        symbol.id
        for parsed in parsed_files.values()
        for symbol in parsed.symbols
        if not symbol.is_declaration
    )
    return frozenset(sym_id for sym_id, count in defined.items() if count > 1)


def _own_member_call(caller_id: str, own: str, call: CallSite) -> ResolvedCall:
    """An anonymous class body's call to its own member *own*, before any flat name.

    A same-file top-level function of the name included. *own* may be the
    caller itself: recursion.
    """
    return ResolvedCall(caller_id, own, 0.95, call.line, "same_file")


def _name_pattern(name: str, *, call: bool) -> re.Pattern[str]:
    """*name* as a word, followed by its argument list (generics allowed) when *call*."""
    tail = r"\s*(?:<[^<>()]*>\s*)?\(" if call else ""
    return re.compile(rf"\b{re.escape(name)}\b{tail}")


def _written_bare(lines: list[str], caller: Symbol, call: CallSite) -> bool:
    """Does *call*'s line call its name with no receiver or qualifier before it?

    The caller's own declaration names it too and is not a call: the first
    line from the caller's start that names it holds the declaration.
    """
    if not 0 < call.line <= len(lines):
        return False
    pattern = _name_pattern(call.target_name, call=True)
    text = lines[call.line - 1]
    found = list(pattern.finditer(text))
    declaration_line = next(
        (i + 1 for i in range(caller.start_line - 1, call.line) if pattern.search(lines[i])),
        None,
    )
    if declaration_line == call.line:
        found = found[1:]
    return any(_unqualified(text[: m.start()]) for m in found)


# An all-caps macro around a call (``FMT_POSIX_CALL(dup(fd))``) may rewrite it,
# typically into ``::dup``.
_MACRO_OPENING = re.compile(r"\b[A-Z][A-Z0-9_]+\s*\([^()]*$")


def _unqualified(before: str) -> bool:
    """Is a name preceded by *before* written with no receiver, qualifier or macro around it?"""
    return not before.rstrip().endswith((".", "::", "->")) and not _MACRO_OPENING.search(before)


def _declared_explicitly(lines: list[str], caller: Symbol, name: str) -> bool:
    """Is the caller declared under a qualified name, ``Owner.name(``?"""
    pattern = _name_pattern(name, call=True)
    for line in lines[caller.start_line - 1 : caller.end_line]:
        found = pattern.search(line)
        if found is not None:
            return line[: found.start()].rstrip().endswith(".")
    return False


def _imports_name(lines: list[str], caller: Symbol, name: str) -> bool:
    """Does the caller's body import *name* (``from m import name``), shadowing itself?"""
    pattern = _name_pattern(name, call=False)
    body = lines[caller.start_line : caller.end_line]
    return any("import" in line.split() and pattern.search(line) for line in body)


def _rivals_a_class_method(symbol_id: str) -> bool:
    """Another class's ordinary method — the one shape that proves last-wins.

    Not a top-level function (Kotlin and C++ put these beside classes, and a
    bare call may genuinely mean one) and not a constructor (name equals its
    parent's), which ``new Entry()`` should reach even inside a class nesting
    its own ``Entry``.
    """
    parts = symbol_id.split("::")
    return len(parts) >= 3 and id_segment_name(parts[-1]) != id_segment_name(parts[-2])


def _extract_class_from_symbol_id(symbol_id: str) -> str | None:
    """The parent from a symbol ID like 'path::ClassName::method'.

    Every segment between path and name, the key ``_file_methods`` uses: an
    anonymous class or enum-constant body spans several (``Op::PLUS``).
    """
    parts = symbol_id.split("::")
    if len(parts) >= 3:
        return "::".join(parts[1:-1])
    return None
