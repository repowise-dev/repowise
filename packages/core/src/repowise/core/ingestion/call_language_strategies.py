"""Per-language call strategies mixed into ``CallResolver``.

Each resolves what a language-neutral tier cannot: package siblings, build
targets and crate roots that no import statement names.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from .call_receiver_typing import (
    _BODY_TYPE_CACHE_ENTRIES,
    _FUNCTION_KINDS,
    _TYPE_KINDS,
    _store_capped,
)
from .lang_helpers.kotlin_lambdas import in_lambda
from .languages.csharp_same_namespace import _BCL_COMMON_TYPES, _USING_ALIAS_RE, _USING_NS_RE
from .languages.receiver_types import dart_named_constructors
from .models import CallSite, ParsedFile, Symbol
from .resolved_call import ResolvedCall
from .resolvers.dotnet.namespace_map import declared_namespaces, scan_type_declarations
from .resolvers.jvm_gradle import (
    SourceSetGraph,
    build_source_set_graph,
    declaration_rank,
    source_set_dir,
)
from .symbol_identity import outer_scope
from .type_names import bare_type_name, dotted_type_segments


@dataclass(frozen=True, slots=True)
class _LanguageCallStrategies:
    """What a language resolves that the language-neutral tiers cannot.

    ``free`` runs after the same-file tier and before the import tiers;
    ``member`` runs before every receiver strategy; ``member_fallback`` runs
    after all of them, so a strategy there only ever sees a call nothing else
    claimed and can only add an edge. ``constructor`` runs ahead of ``member``,
    even for a name no symbol carries, since a named constructor is no symbol.
    ``free_fallback`` runs after the import and inherited tiers and before the
    global one, so it sees only a bare call nothing grounded claimed.
    All five stop at the first hit.
    Strategies are named rather than bound so a probe can substitute one.

    ``member_bare_fallback`` lets a member or chained call nothing claimed fall
    back to the bare-name tiers; ``chain_bare_fallback`` withholds that from a
    call chained on another call (``a.b().c()``) alone, which is resolved by
    its inner call's return type or not at all. ``inherited_typed_receiver`` lets a typed
    receiver whose type lacks the method answer with the one ancestor that
    declares it; ``inherited_fields`` lets a receiver be typed from a field an
    ancestor declares.
    """

    free: tuple[str, ...] = ()
    free_fallback: tuple[str, ...] = ()
    constructor: tuple[str, ...] = ()
    member: tuple[str, ...] = ()
    member_fallback: tuple[str, ...] = ()
    member_bare_fallback: bool = True
    chain_bare_fallback: bool = True
    inherited_typed_receiver: bool = False
    inherited_fields: bool = False


_NO_LANGUAGE_STRATEGIES = _LanguageCallStrategies()

# Languages whose builds are told apart by ``build_roots``.
_BUILD_ROOT_LANGUAGES = frozenset({"dart", "java", "kotlin", "scala", "swift"})

_TYPED_RECEIVER = ("_resolve_typed_receiver",)

# Kotlin Multiplatform's common declaration.
_EXPECT = "expect"


_FRIEND = re.compile(r"\bfriend\b")


def _is_member_function(symbol: Symbol | None) -> bool:
    """A method other than a constructor, which a bare call may still name."""
    return (
        symbol is not None
        and symbol.kind == "method"
        and bool(symbol.parent_name)
        and symbol.name != bare_type_name(symbol.parent_name or "")
    )


def _is_free_function(symbol: Symbol | None) -> bool:
    """A function outside every class, not a macro body (``TEST_F``) that may sit in one."""
    return (
        symbol is not None
        and symbol.kind == "function"
        and not symbol.parent_name
        and not symbol.name.isupper()
    )


def _gradle_script_receiver(path: str) -> str | None:
    """The ``this`` a Gradle Kotlin script's own code runs with."""
    if not path.endswith(".gradle.kts"):
        return None
    return "Settings" if path.rpartition("/")[2] == "settings.gradle.kts" else "Project"


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
    # package qualifier never reaches the typed fallback.
    "go": _LanguageCallStrategies(
        free=("_resolve_go_same_package",),
        member=("_resolve_go_package_call",),
        member_fallback=_TYPED_RECEIVER,
    ),
    # Kotlin shares the JVM tiers and the typed-receiver fallback. Java takes
    # the uniqueness-gated package tier, Kotlin the open one; see
    # ``_resolve_java_same_package_unique`` for why that is a language rule.
    # A Java member call never reaches the bare-name tiers: measured on jsoup
    # and gson, they bound such calls at 0.38 site precision. Kotlin is
    # unmeasured, so it keeps both defaults.
    "java": replace(
        _JVM_STRATEGIES,
        free=("_resolve_java_same_package_unique",),
        member=("_resolve_java_member_type_call", "_resolve_jvm_receiver_same_package"),
        member_fallback=_TYPED_RECEIVER,
        member_bare_fallback=False,
        inherited_typed_receiver=True,
        inherited_fields=True,
    ),
    # A chained call keeps no bare-name fallback: ``.send()`` on a result the
    # return type does not type is not the repo's ``send``.
    "kotlin": replace(
        _JVM_STRATEGIES, member_fallback=_TYPED_RECEIVER, chain_bare_fallback=False
    ),
    "csharp": _LanguageCallStrategies(
        free_fallback=("_resolve_csharp_constructed_type",),
        member_fallback=_TYPED_RECEIVER,
        inherited_fields=True,
    ),
    "python": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # Swift and Dart register the fallback: neither has a package tier of its
    # own, so a typed receiver is looked for in the caller's file, in what the
    # file imports, and then in the global pair index. A Dart chained call is
    # resolved by its inner call's return type or not at all: on pub its bare
    # name bound `File(p).statSync()` and `.then()` to repo functions. The
    # flag reaches chains only, since no Dart member call has a bare twin.
    "swift": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "dart": _LanguageCallStrategies(
        constructor=("_resolve_dart_named_constructor",),
        member_fallback=_TYPED_RECEIVER,
        member_bare_fallback=False,
    ),
    # TypeScript's typed fallback also walks a dotted receiver's fields.
    "typescript": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # Rust types a local or parameter only; a `self.field` receiver is not
    # walked, since Rust has no `_CHAIN_SELF` row.
    "rust": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "cpp": _CPP_STRATEGIES,
    "c": _CPP_STRATEGIES,
    # Apex resolves type names in its project namespace; no bare-name fallback (Java's rule).
    "apex": _LanguageCallStrategies(
        free=("_resolve_apex_namespace_type",),
        member=("_resolve_apex_receiver_type",),
        member_fallback=_TYPED_RECEIVER,
        member_bare_fallback=False,
        inherited_typed_receiver=True,
        inherited_fields=True,
    ),
}


def _csharp_lookup_levels(
    text: str, project_usings: frozenset[str]
) -> tuple[tuple[frozenset[str], str, float], ...]:
    """Where C# looks for a bare type name in *text*, as ``(namespaces, origin, confidence)``.

    Each namespace the file declares and each one around it, innermost first,
    then the global namespace with every ``using`` as one level: two types the
    usings bring in are ambiguous to the compiler too. Ceiling: the text is
    read as one scope, so a ``using`` inside a namespace block counts at file
    level and a file declaring two namespaces sees both chains.
    """
    chain = {
        ns.rsplit(".", depth)[0]
        for ns in declared_namespaces(text)
        for depth in range(ns.count(".") + 1)
    }
    innermost_first = sorted(chain, key=lambda ns: -ns.count("."))
    # ``using static`` names a type, whose members are not types in scope.
    usings = {m.group(1) for m in _USING_NS_RE.finditer(text) if "static" not in m.group(0)}
    return (
        *((frozenset({ns}), "same_package", 0.90) for ns in innermost_first),
        (frozenset({"", *usings, *project_usings}), "import_merged", 0.85),
    )


def _type_argument_count(line: str, at: int) -> int:
    """The type arguments a ``<..>`` at *at* in *line* lists, 0 when none opens there."""
    if line[at : at + 1] != "<":
        return 0
    depth, count = 0, 1
    for char in line[at:]:
        depth += (char == "<") - (char == ">")
        if depth == 0:
            return count
        count += char == "," and depth == 1
    return count


class LanguageStrategiesMixin:
    """Language-specific strategies and the lazy workspace indexes they read."""

    def _init_workspace_indexes(self, repo_path: str | None) -> None:
        # Rust cross-crate resolution
        self._repo_path = repo_path
        self._rust_crate_src: dict[str, str] | None = None  # lazy

        # Go package-scoped resolution (lazy GoPackageIndex). ``_go_index``
        # holds the built index; ``_go_index_built`` distinguishes "not yet
        # built" from "built but unavailable" (no repo_path / no go files).
        self._go_index: Any = None
        self._go_index_built = False

        # Whether any symbol is a Kotlin Multiplatform ``expect``; None until asked.
        self._has_expect: bool | None = None

        # Kotlin Multiplatform source-set dependencies; None until asked.
        self._source_set_graph: SourceSetGraph | None = None

        # {Dart class id: the named constructors its body declares}
        self._dart_constructors: dict[str, frozenset[str]] = {}

        # JVM same-package resolution (lazy JvmWorkspaceIndex)
        self._jvm_index: Any = None
        self._jvm_index_built = False

        # Apex project namespaces (lazy ApexNamespaces)
        self._apex_namespaces: Any = None
        self._apex_retyping = False

        # C/C++ same-target resolution (lazy CppWorkspaceIndex)
        self._cpp_index: Any = None
        self._cpp_index_built = False

        # {C# file: {top-level type name: namespace}}, read off its source, and
        # {C# file: where a bare type name in it is looked up}.
        self._csharp_type_namespaces: dict[str, dict[str, str]] = {}
        self._csharp_lookups: dict[
            str, tuple[frozenset[str], tuple[tuple[frozenset[str], str, float], ...]]
        ] = {}

        # Independent builds in the repo (lazy BuildRoots), and the build roots
        # each file's imports land in.
        self._build_roots: Any = None
        self._imported_roots: dict[str, frozenset[str | None]] = {}

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

    def _resolve_cpp_scoped_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``Qualifier::name()`` against the class the qualifier names.

        The qualifier is written at the call site, so this infers nothing: the
        repository either declares ``Qualifier::name`` or it does not.

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
        # The qualifier names the caller's own class: recursion, when nothing else answers to the id.
        if self._refuses_recursion(sym_id, caller_id, call):
            return None
        return ResolvedCall(caller_id, sym_id, 0.93, call.line, "scoped_name")

    def _cpp_names_no_repo_member(self, file_path: str, call: CallSite, callee_id: str) -> bool:
        """Can a receiver-less C/C++ call not mean *callee_id*, whatever its name?

        ``std::find(..)`` is the standard library's, never a repo symbol that
        shares the name. A bare ``count(..)`` in a function outside every class
        cannot reach a member function either: with no ``this`` and no
        qualifier, only a free function or a ``using namespace`` import answers.
        A constructor is still reached (``Slice(s)``). A caller named in
        capitals is a macro body (``TEST_F``), which may sit inside a class,
        and a caller that is not a function may be a class whose member bodies
        the parse folded in, so both are left alone.
        """
        if self._language_of(file_path) not in ("c", "cpp"):
            return False
        scope = call.scope_name or ""
        if scope == "std" or scope.startswith("std::"):
            return True
        if call.receiver_call is not None or scope:
            return False
        callee = self._symbols_by_id.get(callee_id)
        caller = self._symbols_by_id.get(call.caller_symbol_id or "")
        return _is_free_function(caller) and self._is_cpp_member_function(callee)

    def _is_cpp_member_function(self, symbol: Symbol | None) -> bool:
        """Is *symbol* a member function a bare call from outside its class cannot reach?

        Not when its parent names no repo type: ``int util::clamp()`` is a
        namespace's free function. Nor a ``friend`` defined in the class body,
        which lives at namespace scope and is found by argument lookup. And
        only a body inside its class, or one whose own line writes
        ``Parent::name``: a macro before a C return type
        (``INLINE rtree_t\\nrtree_read(..)``) can parse as a member of that type.
        """
        if not _is_member_function(symbol):
            return False
        assert symbol is not None
        parent = bare_type_name(symbol.parent_name or "")
        if not self._declared_types_named(parent):
            return False
        path = self._symbol_paths_by_id.get(symbol.id, "")
        lines = self._text_of(path).splitlines()
        head = lines[symbol.start_line - 1] if 0 < symbol.start_line <= len(lines) else ""
        if _FRIEND.search(head):
            return False
        span = self._spans_for(path).get(self._only_type_in(path, parent) or "")
        return f"::{symbol.name}" in head or (
            span is not None and span[0] <= symbol.start_line <= span[1]
        )

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
            if (
                sym_id is not None
                and sym_id != caller_id
                and self._reachable_by_name(file_path, sym_id)
            ):
                return ResolvedCall(caller_id, sym_id, 0.85, call.line, "same_target")
        return None

    def _in_reach_of_build(self, file_path: str, target_path: str) -> bool:
        """May a bare name in *file_path* mean a symbol in *target_path*, by name alone?

        Not across independent builds, unless the caller's build composes the
        target's in or the caller imports a file there. A Gradle build
        script's classpath is its build logic, not the sources it builds, so a
        script's name reaches only into a build its own composes in.
        """
        if self._language_of(file_path) not in _BUILD_ROOT_LANGUAGES:
            return True
        if self._build_roots is None:
            from .build_roots import BuildRoots

            self._build_roots = BuildRoots(self._repo_path, self._parsed_files)
        roots = self._build_roots
        other = roots.root_of(target_path)
        if file_path.endswith(".gradle.kts"):
            return roots.script_sees(file_path, other)
        own = roots.root_of(file_path)
        return not roots.separates(own, other) or other in self._roots_imported_by(file_path)

    def _roots_imported_by(self, file_path: str) -> frozenset[str | None]:
        """The build roots *file_path*'s imports land in, re-exports followed."""
        imported = self._imported_roots.get(file_path)
        if imported is None:
            assert self._build_roots is not None
            imported = frozenset(map(self._build_roots.root_of, self._visible_files(file_path)))
            self._imported_roots[file_path] = imported
        return imported

    def _visible_files(self, file_path: str) -> set[str]:
        """The files *file_path* imports, and every file those re-export (Dart ``export``)."""
        seen: set[str] = set()
        pending = list(self._import_targets.get(file_path, ()))
        while pending:
            target = pending.pop()
            if target in seen:
                continue
            seen.add(target)
            parsed = self._parsed_files.get(target)
            pending.extend(
                imp.resolved_file
                for imp in (parsed.imports if parsed else ())
                if imp.is_reexport and imp.resolved_file in self._parsed_files
            )
        return seen

    def _same_package_siblings(self, index: Any, file_path: str) -> list[str]:
        """*file_path*'s JVM package siblings within its own build."""
        return [
            sibling
            for sibling in index.same_package_files(file_path)
            if self._in_reach_of_build(file_path, sibling)
        ]

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
        whose receiver the grammar dropped. Refusing is not deleting: the chain
        continues into the import tiers.
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
        found = (
            (sibling, sym_id)
            for sibling in self._same_package_siblings(index, file_path)
            if (sym_id := self._package_symbol(sibling, call.target_name, caller_id)) is not None
        )
        if unique_only:
            # Two siblings declare it and, for Java, nothing here can tell them apart.
            first, second = next(found, None), next(found, None)
            sym_id = first[1] if first is not None and second is None else None
        else:
            sym_id = self._most_visible(file_path, found)
        if sym_id is None:
            return None
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "same_package")

    def _most_visible(self, file_path: str, found: Iterator[tuple[str, str]]) -> str | None:
        """The first of *found*'s ``(file, symbol id)`` declarations *file_path* sees most directly.

        A Kotlin Multiplatform name has one declaration per source set; see
        ``declaration_rank``. *found* is read lazily: the first declaration
        answers when no rank can tell them apart, and one in the caller's own
        source set ends the search. A declaration in a source set the caller
        cannot see is skipped.
        """
        if source_set_dir(file_path) is None and not self._repo_declares_expect():
            return next(found, (None, None))[1]
        graph = self._get_source_set_graph()
        best: str | None = None
        best_rank: int | None = None
        for path, sym_id in found:
            rank = declaration_rank(file_path, path, self._is_expect(sym_id), graph)
            if rank is None:
                continue
            if rank == 0:
                return sym_id
            if best_rank is None or rank < best_rank:
                best, best_rank = sym_id, rank
        return best

    def _get_source_set_graph(self) -> SourceSetGraph:
        """The repository's Kotlin Multiplatform source-set dependencies. Built once."""
        if self._source_set_graph is None:
            from pathlib import Path

            self._source_set_graph = build_source_set_graph(
                self._parsed_files, Path(self._repo_path) if self._repo_path else None
            )
        return self._source_set_graph

    def _is_expect(self, sym_id: str) -> bool:
        """Is *sym_id* an ``expect`` declaration, or a member of an ``expect`` type?"""
        symbol = self._symbols_by_id[sym_id]
        if _EXPECT in symbol.modifiers:
            return True
        if symbol.parent_name is None:
            return False
        owner = self._symbols_by_id.get(self._caller_class_id(sym_id))
        return owner is not None and _EXPECT in owner.modifiers

    def _repo_declares_expect(self) -> bool:
        """Does any repository symbol carry ``expect``? Asked once."""
        if self._has_expect is None:
            self._has_expect = any(_EXPECT in s.modifiers for s in self._symbols_by_id.values())
        return self._has_expect

    def _package_symbol(self, sibling: str, name: str, caller_id: str) -> str | None:
        """What *name* means in *sibling* to a bare call from another package file.

        The package shares top-level names, not members: a data class's
        synthesized ``copy`` or a ViewModel's ``onClick`` is reached through a
        receiver, an ancestor or a scope function, never by the package alone.
        A nested type and a constructor (named for its class) are not members.
        A member indexed last under the name hides no top-level symbol of it.
        """
        sym_id = self._file_symbols.get(sibling, {}).get(name)
        if sym_id is None:
            return None
        symbol = self._symbols_by_id[sym_id]
        parent = symbol.parent_name
        if parent is not None and symbol.name != parent and symbol.kind not in _TYPE_KINDS:
            sym_id = self._file_top_level.get(sibling, {}).get(name)
        return None if sym_id == caller_id else sym_id

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
        found = (
            (sibling, sym_id)
            for sibling in self._same_package_siblings(index, file_path)
            if (sym_id := self._file_methods.get(sibling, {}).get(key)) is not None
        )
        sym_id = self._most_visible(file_path, found)
        if sym_id is None:
            return None
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "receiver_same_package")

    def _resolve_java_member_type_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``Outer.Inner.method()``: the method of the type the whole path names.

        The path is read as Java reads it from the caller, so the ``Inner`` of
        another imported type that also nests one is never taken, and a type
        that lacks the method answers nothing. A path naming no repository
        type (an enum constant, an external type) is resolved as its
        receiver-less copy was before the grammar captured the path.
        """
        receiver = call.receiver_name or ""
        if "." not in receiver:
            return None
        type_id = self._java_written_type(file_path, caller_id, receiver)
        if type_id is None:
            return self._resolve_one(file_path, replace(call, receiver_name=None))
        found = self._java_member_target(file_path, call, caller_id, type_id)
        if found is None:
            return None
        sym_id, scope = found
        # ``receiver_same_file`` / ``_same_package`` / ``_import``: the type is written here.
        confidence = {"same_file": 0.93, "same_package": 0.90}.get(scope, 0.88)
        return ResolvedCall(caller_id, sym_id, confidence, call.line, f"receiver_{scope}")

    def _resolve_go_package_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve ``pkg.Func()`` against *every* file in the package.

        The module-alias strategy resolves only against the single file the
        import resolved to, which misses a function in a sibling file of the
        package, so look across the whole package via the GoPackageIndex.
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
            # A package qualifier reaches package-level names, never a method.
            syms = self._file_top_level.get(sibling, {})
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

    def _get_apex_namespaces(self) -> Any:
        """Lazily build the ApexNamespaces index."""
        if self._apex_namespaces is None:
            from .languages.apex_namespace import build_apex_namespaces

            self._apex_namespaces = build_apex_namespaces(self._parsed_files)
        return self._apex_namespaces

    def _apex_type(self, file_path: str, caller_id: str, written: str) -> str | None:
        """The type id an Apex type path (``Foo``, ``Outer.Inner``, ``ns.Foo``) names here.

        The head is a member type of the caller's class or of a class around
        it (an inner class wins inside its outer), else the one top-level type
        of that name the caller's project reaches. A head naming no type is a
        namespace prefix (see ``prefixed_type``). Each later segment is a
        member type of the one before it.
        """
        index = self._get_apex_namespaces()
        head, *rest = dotted_type_segments(written)
        type_id = self._apex_head_type(file_path, caller_id, head)
        if type_id is None and rest:
            prefix, (head, *rest) = head, rest
            type_id = index.prefixed_type(file_path, prefix, head)
        for segment in rest:
            if type_id is None:
                return None
            type_id = index.member_type(type_id, segment)
        return type_id

    def _apex_head_type(self, file_path: str, caller_id: str, name: str) -> str | None:
        """The type a bare Apex type name means in *caller_id*.

        A member type of the caller's class, or of one of its ancestors (a
        handler subclass writes its base's ``DmlWrapper`` bare), then the same
        for each class around it, then a top-level type.
        """
        index = self._get_apex_namespaces()
        caller = self._symbols_by_id.get(caller_id)
        scope = (
            caller_id
            if caller is not None and caller.kind in _TYPE_KINDS
            else self._caller_class_id(caller_id)
        )
        while "::" in scope:
            for owner in (scope, *self._ancestors_of(scope)):
                member = index.member_type(owner, name)
                if member is not None:
                    return member
            scope = outer_scope(scope)
        return index.top_level_type(file_path, name)

    def _apex_type_call(
        self, file_path: str, caller_id: str, type_id: str, line: int, origin: str
    ) -> ResolvedCall | None:
        """``new T(..)``: *type_id*'s constructors when it declares any, else the type."""
        symbol = self._symbols_by_id[type_id]
        path = self._symbol_paths_by_id[type_id]
        target = self._file_methods.get(path, {}).get((symbol.name, symbol.name), type_id)
        if target == caller_id:
            return None
        if path == file_path:
            return ResolvedCall(caller_id, target, 0.93, line, "receiver_same_file")
        return ResolvedCall(caller_id, target, 0.90, line, origin)

    def _resolve_apex_namespace_type(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``new Foo(..)``: the type its name means in the caller's project.

        A bare Apex name reaches no other class's method, so only a type is
        looked up. The class and its constructors share the name, which is
        why the uniqueness-gated global tier refuses this call.
        """
        type_id = self._apex_head_type(file_path, caller_id, call.target_name)
        # Only the type's own spelling: ``execute()`` inherited from a platform
        # class is not ``new Execute()``.
        if type_id is None or self._symbols_by_id[type_id].name != call.target_name:
            return None
        hit = self._apex_type_call(file_path, caller_id, type_id, call.line, "same_package")
        if hit is not None and hit.origin == "receiver_same_file":
            return replace(hit, confidence=0.95, origin="same_file")
        return hit

    def _resolve_apex_receiver_type(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``Foo.m()``, ``Outer.Inner.m()``, ``new Outer.Inner()``: the receiver names a type.

        A receiver the caller declares as a local, parameter or field is a
        variable, whatever class shares its folded name: it is left to the
        typed fallback. ``this`` and ``super`` are left to their own tiers.
        """
        receiver = call.receiver_name or ""
        if receiver in ("this", "super") or self._apex_variable(file_path, caller_id, receiver):
            return None
        type_id = self._apex_type(file_path, caller_id, receiver)
        if type_id is None:
            # ``Affiliations.get()`` on a local declared ``affiliations``.
            variable = self._apex_variable_spelling(file_path, caller_id, receiver)
            # One hop: the typed lookup asks this strategy again with the declared type.
            if variable is None or self._apex_retyping:
                return None
            self._apex_retyping = True
            try:
                return self._resolve_typed_receiver(
                    file_path, replace(call, receiver_name=variable), caller_id
                )
            finally:
                self._apex_retyping = False
        return self._apex_member_call(file_path, call, caller_id, type_id)

    def _apex_member_call(
        self, file_path: str, call: CallSite, caller_id: str, type_id: str
    ) -> ResolvedCall | None:
        """The member *call* names on the type *type_id*: a member type, a method, an inherited one.

        The name as written first: ``X.errors()`` is the method even where
        ``X`` also nests a class ``Errors``, which ``new X.Errors()`` means.
        """
        sym_id = self._declares(type_id, call.target_name)
        if sym_id is None:
            member = self._get_apex_namespaces().member_type(type_id, call.target_name)
        else:
            member = sym_id if self._symbols_by_id[sym_id].kind in _TYPE_KINDS else None
        if member is not None:
            return self._apex_type_call(
                file_path, caller_id, member, call.line, "receiver_same_package"
            )
        # A property of the name is data, never the callee: an ancestor's method may be.
        if sym_id is None or self._symbols_by_id[sym_id].kind not in _FUNCTION_KINDS:
            sym_id = self._ancestor_method(
                type_id, call.target_name, caller_id, class_chain_first=True
            )
        if sym_id is None or sym_id == caller_id:
            return None
        if self._symbols_by_id[sym_id].kind not in _FUNCTION_KINDS:
            return None
        if self._symbol_paths_by_id.get(sym_id) == file_path:
            return ResolvedCall(caller_id, sym_id, 0.93, call.line, "receiver_same_file")
        return ResolvedCall(caller_id, sym_id, 0.90, call.line, "receiver_same_package")

    def _apex_variable_spelling(self, file_path: str, caller_id: str, receiver: str) -> str | None:
        """The local, parameter or field *receiver* names in another case, as declared."""
        if "." in receiver:
            return None
        declared = (
            *self._declared_types_in(file_path, caller_id, "apex"),
            *self._field_types_in(file_path, "apex").get(self._caller_class_id(caller_id), {}),
        )
        folded = receiver.lower()
        return next((name for name in declared if name.lower() == folded and name != receiver), None)

    def _apex_variable(self, file_path: str, caller_id: str, receiver: str) -> bool:
        """Is the head of *receiver* a local, parameter or field the caller declares?"""
        head = receiver.partition(".")[0]
        type_name, _, _ = self._receiver_type_and_scope(file_path, caller_id, "apex", head)
        return type_name is not None

    def _resolve_csharp_constructed_type(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``new Foo(..)`` that no import tier reached: the one type of that name in scope.

        C# finds a bare type name in the caller's namespace, then in each one
        around it, then in the namespaces a ``using`` brings in: the file's
        own, every ``global using`` in its project and the project's MSBuild
        ``<Using>`` items. Two types at the first level holding one refuse.
        With none visible, a name the repository declares once is taken, as
        the global tier would were the constructor not a second symbol of
        that name.
        """
        name = call.target_name
        if call.receiver_name or name not in self._csharp_type_names:
            return None
        arity = self._constructed_arity(file_path, call)
        found = None if arity is None else self._csharp_type_in_scope(file_path, name, arity)
        if found is None:
            return None
        type_id, origin, confidence = found
        return self._csharp_type_call(type_id, caller_id, call, origin, confidence)

    def _constructed_arity(self, file_path: str, call: CallSite) -> int | None:
        """How many type arguments *call*'s line writes after ``new Name``, None for no ``new``.

        A site records neither ``new`` nor its type arguments, and a bare
        ``Foo()`` may be a local function or a ``using static`` method no
        tier indexed.
        """
        lines = self._lines_of(file_path)
        line = lines[call.line - 1] if 0 < call.line <= len(lines) else ""
        match = re.search(rf"\bnew\s+{re.escape(call.target_name)}\b\s*", line)
        if match is None:
            return None
        return _type_argument_count(line, match.end())

    def _csharp_type_in_scope(
        self, file_path: str, name: str, arity: int
    ) -> tuple[str, str, float] | None:
        """``(type id, origin, confidence)`` of the one type *name* with *arity* means here."""
        types = [
            (ns, type_id) for ns, type_id, n in self._csharp_constructible_types(name) if n == arity
        ]
        aliases, levels = self._csharp_lookup(file_path)
        if not types or name in aliases:  # ``using Foo = X.Bar;`` shadows every type Foo
            return None
        for level, origin, confidence in levels:
            visible = [type_id for namespace, type_id in types if namespace in level]
            if visible:
                return (visible[0], origin, confidence) if len(visible) == 1 else None
        if len(types) > 1 or name in _BCL_COMMON_TYPES:
            return None
        return types[0][1], "global_unique", 0.50

    def _csharp_lookup(
        self, file_path: str
    ) -> tuple[frozenset[str], tuple[tuple[frozenset[str], str, float], ...]]:
        """*file_path*'s ``using`` aliases and the levels a bare type name is looked up in."""
        found = self._csharp_lookups.get(file_path)
        if found is None:
            text = self._text_of(file_path)
            found = (
                frozenset(m.group(1) for m in _USING_ALIAS_RE.finditer(text)),
                _csharp_lookup_levels(
                    text, self._csharp_project_usings.get(file_path, frozenset())
                ),
            )
            self._csharp_lookups[file_path] = found
        return found

    def _csharp_constructible_types(self, name: str) -> list[tuple[str, str, int]]:
        """``(namespace, type id, arity)`` for each top-level C# class or struct named *name*.

        The fragments of one ``partial`` type count once, under the first;
        ``Box`` and ``Box<T>`` are two types.
        """
        found: dict[tuple[str, tuple[str, ...], int], str] = {}
        for sym_id in self._global_symbols.get(name, ()):
            symbol = self._symbols_by_id[sym_id]
            if symbol.language != "csharp" or symbol.kind not in ("class", "struct"):
                continue
            path = self._symbol_paths_by_id[sym_id]
            namespace = self._csharp_top_level_types(path).get(name)
            if namespace is None:  # nested: visible by bare name only inside its outer type
                continue
            fragments = self._partial_fragments.get((path, name)) or (path,)
            found.setdefault((namespace, fragments, symbol.type_parameter_count or 0), sym_id)
        return [(namespace, sym_id, arity) for (namespace, _, arity), sym_id in found.items()]

    def _csharp_top_level_types(self, path: str) -> dict[str, str]:
        """``{type name: namespace}`` for the top-level types *path* declares."""
        types = self._csharp_type_namespaces.get(path)
        if types is None:
            types = {
                decl.name: decl.namespace
                for decl in scan_type_declarations(self._text_of(path))
                if decl.qualified == decl.name
            }
            self._csharp_type_namespaces[path] = types
        return types

    def _csharp_type_call(
        self, type_id: str, caller_id: str, call: CallSite, origin: str, confidence: float
    ) -> ResolvedCall | None:
        """``new T(..)``: *type_id*'s constructor, else the type.

        Also the type when ``partial`` fragments each declare constructors:
        an overload set is chosen within one file, so it could not pick
        among them.
        """
        name = call.target_name
        path = self._symbol_paths_by_id[type_id]
        # A generic type beside a same-named plain one owns its members as ``Box`1``.
        arity = self._symbols_by_id[type_id].type_parameter_count or 0
        owner = f"{name}`{arity}" if arity and type_id.endswith(f"`{arity}") else name
        ctors = [
            ctor
            for fragment in self._partial_fragments.get((path, name)) or (path,)
            if (ctor := self._file_methods.get(fragment, {}).get((owner, name))) is not None
        ]
        target = ctors[0] if len(ctors) == 1 else type_id
        if target == caller_id:
            return None
        return ResolvedCall(caller_id, target, confidence, call.line, origin)

    def _resolve_dart_named_constructor(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """``Foo.named(..)`` or ``p.Foo.named(..)``: the class, when its body declares it.

        A Dart named or factory constructor is no symbol, so the call binds to
        its class, as ``Foo(..)`` does. A static member of the name is a symbol
        and is left to the member tiers. Through an import prefix the class is
        the one the prefixed library declares, and the edge is a
        ``module_alias`` one, as ``p.Foo(..)`` draws.
        """
        prefix, _, class_name = (call.receiver_name or "").rpartition(".")
        if prefix:
            return self._resolve_dart_prefixed_constructor(
                file_path, call, caller_id, prefix, class_name
            )
        if class_name not in self._known_type_names:
            return None
        hit = self._class_seen_from(file_path, caller_id, class_name, call.line)
        if hit is None or self._declares(hit.callee_id, call.target_name) is not None:
            return None
        return hit if call.target_name in self._dart_constructors_of(hit.callee_id) else None

    def _resolve_dart_prefixed_constructor(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
        prefix: str,
        class_name: str,
    ) -> ResolvedCall | None:
        """``p.Foo.named(..)``: *prefix* must be an import prefix of the caller's file.

        Only ``prefix.Class`` is read: a longer path is a field chain, not a
        library qualifier. The class is looked up in the prefixed library and
        what it re-exports, and must declare the constructor.
        """
        module_file = self._module_aliases.get(file_path, {}).get(prefix)
        if module_file is None or "." in prefix or class_name not in self._known_type_names:
            return None
        declaring = self._barrel_origins.get(module_file, {}).get(class_name) or module_file
        class_id = self._only_type_in(declaring, class_name)
        if class_id is None or self._declares(class_id, call.target_name) is not None:
            return None
        if call.target_name not in self._dart_constructors_of(class_id):
            return None
        return ResolvedCall(caller_id, class_id, 0.88, call.line, "module_alias")

    def _dart_constructors_of(self, class_id: str) -> frozenset[str]:
        """The named constructors the Dart class *class_id* declares, scanned once."""
        found = self._dart_constructors.get(class_id)
        if found is None:
            class_file = self._symbol_paths_by_id.get(class_id, "")
            start, end = self._spans_for(class_file).get(class_id, (1, 0))
            body = "\n".join(self._text_of(class_file).splitlines()[start - 1 : end])
            found = dart_named_constructors(body, self._symbols_by_id[class_id].name)
            _store_capped(self._dart_constructors, class_id, found, _BODY_TYPE_CACHE_ENTRIES)
        return found

    def _extension_receiver_member_call(
        self, file_path: str, call: CallSite, caller_id: str
    ) -> ResolvedCall | None:
        """A bare call in a Kotlin extension, read as ``this.name(..)`` on the extended type.

        Inside ``fun A.run() = go()`` the implicit ``this`` is an ``A``, and a
        member of ``A`` (or of one of its ancestors) beats any top-level name an
        import brings in. Only a repo class the name resolves to uniquely is
        asked, and only outside a lambda, whose receiver a text scan cannot
        type. A receiver with no such member answers nothing, so the later
        tiers run as before.
        """
        if call.receiver_name or call.receiver_call is not None or call.scope_name:
            return None
        caller = self._symbols_by_id.get(caller_id)
        if caller is None or caller.language != "kotlin" or caller.kind not in _FUNCTION_KINDS:
            return None
        receiver = self._kotlin_receiver(caller)
        if receiver is None or self._call_in_lambda(file_path, call, caller):
            return None
        class_ids = {
            sym_id
            for sym_id in self._global_symbols.get(receiver, ())
            if (symbol := self._symbols_by_id.get(sym_id)) is not None
            and symbol.kind in _TYPE_KINDS
        }
        if len(class_ids) != 1:
            return None
        class_id = next(iter(class_ids))
        own = self._declares(class_id, call.target_name)
        if self._is_member(own, caller_id):
            return ResolvedCall(caller_id, own, 0.95, call.line, "enclosing_class")
        inherited = self._ancestor_method(class_id, call.target_name, caller_id)
        if self._is_member(inherited, caller_id):
            return ResolvedCall(caller_id, inherited, 0.90, call.line, "enclosing_inherited")
        return None

    def _is_member(self, sym_id: str | None, caller_id: str) -> bool:
        """Is *sym_id* a real member, not the caller or an extension of the type?"""
        symbol = self._symbols_by_id.get(sym_id or "")
        return symbol is not None and sym_id != caller_id and not symbol.extension_receiver

    def _lacks_extension_receiver(self, file_path: str, call: CallSite, callee_id: str) -> bool:
        """Is *callee_id* a Kotlin extension this bare call has no receiver for?

        Written bare, ``digest(..)`` reaches ``fun AuthenticationConfig.digest``
        only where the implicit ``this`` is that type: in a member or an
        extension of it or of a subtype, or in a lambda, whose receiver
        (``routing { .. }``) a text scan cannot type and so never refuses.
        """
        receiver = self._extension_receiver(callee_id)
        if receiver is None:
            return False
        caller = self._symbols_by_id.get(call.caller_symbol_id or "")
        if receiver in self._receivers_in_scope(file_path, caller):
            return False
        return not self._call_in_lambda(file_path, call, caller)

    def _extension_receiver(self, callee_id: str) -> str | None:
        """The repo class a Kotlin extension needs as ``this``.

        None for an external receiver (``List<String>``, a library base the
        caller may extend) or an alias: no repo class says which callers
        have one, so nothing is refused.
        """
        callee = self._symbols_by_id.get(callee_id)
        if callee is None or callee.language != "kotlin" or callee.kind not in _FUNCTION_KINDS:
            return None
        receiver = self._kotlin_receiver(callee)
        return receiver if receiver in self._known_type_names else None

    def _receivers_in_scope(self, file_path: str, caller: Symbol | None) -> set[str]:
        """The bare type names ``this`` may be outside any lambda of *caller*."""
        if caller is not None:
            return self._implicit_receivers(caller)
        script = _gradle_script_receiver(file_path)
        return {script} if script else set()

    def _call_in_lambda(self, file_path: str, call: CallSite, caller: Symbol | None) -> bool:
        """Is *call* inside a lambda of *caller*? True when the text cannot tell."""
        lines = self._text_of(file_path).splitlines()
        start = caller.start_line if caller is not None else 1
        if not 1 <= start <= call.line <= len(lines):
            return True
        last = lines[call.line - 1]
        at = re.search(rf"(?<![\w.]){re.escape(call.target_name)}\b", last)
        source = "\n".join([*lines[start - 1 : call.line - 1], last[: at.start() if at else 0]])
        return in_lambda(source, caller is not None and caller.kind in _FUNCTION_KINDS)

    def _kotlin_receiver(self, symbol: Symbol) -> str | None:
        """The type a Kotlin function extends, as the parser read it off its header."""
        return symbol.extension_receiver

    def _implicit_receivers(self, caller: Symbol) -> set[str]:
        """The bare type names ``this`` may be inside *caller*."""
        extended = self._kotlin_receiver(caller) if caller.language == "kotlin" else None
        names = {extended} if extended else set()
        names.update(self._names_of_classes(self._receiver_classes(caller)))
        parsed = self._parsed_files.get(self._symbol_paths_by_id.get(caller.id, ""))
        names.update(
            bare_type_name(h.parent_name)
            for h in (parsed.heritage if parsed else ())
            if h.child_name in names
        )
        return names

    def _receiver_classes(self, caller: Symbol) -> list[str]:
        """The class whose code *caller* is, then each outer class an ``inner`` one sees."""
        if caller.kind in _TYPE_KINDS:
            class_id = caller.id
        elif caller.parent_name:
            class_id = self._caller_class_id(caller.id)
        else:
            return []
        found = []
        while (symbol := self._symbols_by_id.get(class_id)) is not None:
            found.append(class_id)
            if "inner" not in symbol.modifiers:
                break
            class_id = outer_scope(class_id)
        return found

    def _names_of_classes(self, class_ids: list[str]) -> set[str]:
        """The names of *class_ids* and of their repo ancestors."""
        return {
            symbol.name
            for class_id in class_ids
            for symbol in map(self._symbols_by_id.get, (class_id, *self._ancestors_of(class_id)))
            if symbol is not None
        }
