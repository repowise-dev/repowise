"""Per-language call strategies mixed into ``CallResolver``.

Each resolves what a language-neutral tier cannot: package siblings, build
targets and crate roots that no import statement names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .call_receiver_typing import _FUNCTION_KINDS, _TYPE_KINDS
from .lang_helpers.kotlin_lambdas import in_lambda
from .models import CallSite, ParsedFile, Symbol
from .resolved_call import ResolvedCall
from .symbol_identity import outer_scope
from .type_names import bare_type_name, kotlin_extension_receiver


@dataclass(frozen=True, slots=True)
class _LanguageCallStrategies:
    """What a language resolves that the language-neutral tiers cannot.

    ``free`` runs after the same-file tier and before the import tiers;
    ``member`` runs before every receiver strategy; ``member_fallback`` runs
    after all of them, so a strategy there only ever sees a call nothing else
    claimed and can only add an edge. All three stop at the first hit.
    Strategies are named rather than bound so a probe can substitute one.

    ``member_bare_fallback`` lets a member or chained call nothing claimed fall
    back to the bare-name tiers. ``inherited_typed_receiver`` lets a typed
    receiver whose type lacks the method answer with the one ancestor that
    declares it.
    """

    free: tuple[str, ...] = ()
    member: tuple[str, ...] = ()
    member_fallback: tuple[str, ...] = ()
    member_bare_fallback: bool = True
    inherited_typed_receiver: bool = False


_NO_LANGUAGE_STRATEGIES = _LanguageCallStrategies()

_TYPED_RECEIVER = ("_resolve_typed_receiver",)


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
        member_fallback=_TYPED_RECEIVER,
        member_bare_fallback=False,
        inherited_typed_receiver=True,
    ),
    "kotlin": replace(_JVM_STRATEGIES, member_fallback=_TYPED_RECEIVER),
    "csharp": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "python": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # Swift and Dart register the fallback and nothing else: neither has a
    # package tier of its own, so a typed receiver is looked for in the
    # caller's file, in what the file imports, and then in the global pair index.
    "swift": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "dart": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # TypeScript's typed fallback also walks a dotted receiver's fields.
    "typescript": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    # Rust types a local or parameter only; a `self.field` receiver is not
    # walked, since Rust has no `_CHAIN_SELF` row.
    "rust": _LanguageCallStrategies(member_fallback=_TYPED_RECEIVER),
    "cpp": _CPP_STRATEGIES,
    "c": _CPP_STRATEGIES,
}


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

        # {Kotlin function id: the type it extends, or None}
        self._kotlin_receivers: dict[str, str | None] = {}

        # JVM same-package resolution (lazy JvmWorkspaceIndex)
        self._jvm_index: Any = None
        self._jvm_index_built = False

        # C/C++ same-target resolution (lazy CppWorkspaceIndex)
        self._cpp_index: Any = None
        self._cpp_index_built = False

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
            if (
                sym_id is not None
                and sym_id != caller_id
                and self._reachable_by_name(file_path, sym_id)
            ):
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
        found: str | None = None
        for sibling in index.same_package_files(file_path):
            sym_id = self._package_symbol(sibling, call.target_name, caller_id)
            if sym_id is not None:
                if not unique_only:
                    return ResolvedCall(caller_id, sym_id, 0.90, call.line, "same_package")
                if found is not None:
                    # Two siblings declare it and nothing here can tell them apart.
                    return None
                found = sym_id
        if found is None:
            return None
        return ResolvedCall(caller_id, found, 0.90, call.line, "same_package")

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
        """The type a Kotlin function extends, read off its header (annotations included)."""
        if symbol.id not in self._kotlin_receivers:
            path = self._symbol_paths_by_id.get(symbol.id, "")
            lines = self._text_of(path).splitlines()[symbol.start_line - 1 : symbol.start_line + 7]
            self._kotlin_receivers[symbol.id] = kotlin_extension_receiver(
                " ".join(lines), symbol.name
            )
        return self._kotlin_receivers[symbol.id]

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
