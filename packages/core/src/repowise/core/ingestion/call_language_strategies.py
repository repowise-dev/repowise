"""Per-language call strategies mixed into ``CallResolver``.

Each resolves what a language-neutral tier cannot: package siblings, build
targets and crate roots that no import statement names."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .models import CallSite, ParsedFile
from .resolved_call import ResolvedCall


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


class LanguageStrategiesMixin:
    """Language-specific strategies and the lazy workspace indexes they read."""

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
