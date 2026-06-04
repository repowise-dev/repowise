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

Each resolved call produces a (source_id, target_id, confidence) triple that
the GraphBuilder converts into a CALLS edge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import structlog

from .models import CallSite, NamedBinding, ParsedFile

log = structlog.get_logger(__name__)


def _file_language(parsed_files: dict[str, ParsedFile], symbol_id: str) -> str | None:
    """Extract language from a symbol ID's file via the parsed files map."""
    file_path = symbol_id.split("::")[0] if "::" in symbol_id else symbol_id
    parsed = parsed_files.get(file_path)
    return parsed.file_info.language if parsed else None


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """A call resolved to concrete symbol IDs with a confidence score."""

    caller_id: str  # symbol node ID of the calling function/method
    callee_id: str  # symbol node ID of the called function/method
    confidence: float  # 0.0–1.0
    line: int  # call site line number (for diagnostics)


class CallResolver:
    """Resolve raw CallSites to symbol-level edges.

    Constructed once per ``GraphBuilder.build()`` call with the full set
    of parsed files and import edges. Stateless after construction —
    ``resolve_file()`` can be called concurrently for different files.
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

        # Global class index: {name: symbol_id} — for receiver resolution
        self._global_classes: dict[str, list[str]] = defaultdict(list)

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

        # Also track Rust pub-use re-exports that use wildcard (*) bindings.
        # When a file has `pub use foo::*`, all symbols from foo are
        # transitively available through this file.
        for path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                if not imp.is_reexport or not imp.resolved_file:
                    continue
                if imp.resolved_file.startswith("external:"):
                    continue
                resolved = imp.resolved_file
                source_syms = self._file_symbols.get(resolved, {})
                file_syms = self._file_symbols.get(path, {})
                for sym_name in source_syms:
                    if sym_name not in file_syms:
                        self._barrel_origins[path][sym_name] = resolved

        # Multi-hop: follow chains up to 5 hops
        for _ in range(4):
            changed = False
            for path, origins in list(self._barrel_origins.items()):
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
                self.parsed_files = pf
                self.go_modules: tuple[tuple[str, str], ...] = ()
                self.go_module_path: str | None = None

        self._go_index = build_go_package_index(_Ctx(self._repo_path, self._parsed_files))
        return self._go_index

    def _is_go(self, file_path: str) -> bool:
        parsed = self._parsed_files.get(file_path)
        return bool(parsed and parsed.file_info.language == "go")

    def _is_jvm(self, file_path: str) -> bool:
        parsed = self._parsed_files.get(file_path)
        return bool(parsed and parsed.file_info.language in ("java", "kotlin"))

    def _is_cpp_family(self, file_path: str) -> bool:
        parsed = self._parsed_files.get(file_path)
        return bool(parsed and parsed.file_info.language in ("cpp", "c"))

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
                return ResolvedCall(caller_id, sym_id, 0.85, call.line)
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
                return ResolvedCall(caller_id, sym_id, 0.90, call.line)
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
                return ResolvedCall(caller_id, sym_id, 0.88, call.line)
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
                return ResolvedCall(caller_id, sym_id, 0.90, call.line)
        return None

    def _build_indices(self, parsed_files: dict[str, ParsedFile]) -> None:
        """Build symbol lookup indices from parsed file data.

        (Import-name maps are shared — see ``import_index.build_import_name_maps``.)
        """
        for path, parsed in parsed_files.items():
            file_syms: dict[str, str] = {}
            file_methods: dict[tuple[str, str], str] = {}

            for sym in parsed.symbols:
                # File-level symbol index (top-level symbols and methods)
                file_syms[sym.name] = sym.id

                # Method index: (class_name, method_name) → symbol_id
                if sym.parent_name:
                    key = (sym.parent_name, sym.name)
                    file_methods[key] = sym.id
                    self._global_methods[key].append((path, sym.id))

                # Global indices
                self._global_symbols[sym.name].append(sym.id)
                if sym.kind in ("class", "struct", "interface", "enum"):
                    self._global_classes[sym.name].append(sym.id)

            self._file_symbols[path] = file_syms
            self._file_methods[path] = file_methods

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
                results.append(resolved)

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

    def _resolve_free_call(
        self,
        file_path: str,
        call: CallSite,
        caller_id: str,
    ) -> ResolvedCall | None:
        """Resolve a free function call (no receiver)."""
        target_name = call.target_name

        # Tier 1: same-file
        file_syms = self._file_symbols.get(file_path, {})
        if target_name in file_syms:
            callee_id = file_syms[target_name]
            if callee_id != caller_id:  # no self-recursion edges for now
                return ResolvedCall(caller_id, callee_id, 0.95, call.line)

        # Go: a bare call may target a function defined in a sibling file of
        # the same package (shared namespace, no import). Resolve against the
        # package before the weaker import/global tiers.
        if self._is_go(file_path):
            go_same_pkg = self._resolve_go_same_package(file_path, call, caller_id)
            if go_same_pkg is not None:
                return go_same_pkg

        # JVM: same-package implicit access — classes in the same package
        # reference each other with no import statement.
        if self._is_jvm(file_path):
            jvm_same_pkg = self._resolve_jvm_same_package(file_path, call, caller_id)
            if jvm_same_pkg is not None:
                return jvm_same_pkg

        # C/C++: same-target unqualified access — a bare call may target a
        # function declared in a header consumed by the importer's CMake/
        # Bazel target and defined in any sibling TU of that target.
        if self._is_cpp_family(file_path):
            cpp_same_target = self._resolve_cpp_same_target(file_path, call, caller_id)
            if cpp_same_target is not None:
                return cpp_same_target

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
                return ResolvedCall(caller_id, source_syms[lookup_name], 0.90, call.line)

        # 2a fallback: plain _import_names (for imports without binding data)
        name_to_file = self._import_names.get(file_path, {})
        if target_name in name_to_file and not binding:
            source_file = name_to_file[target_name]
            barrel = self._barrel_origins.get(source_file, {})
            if target_name in barrel:
                source_file = barrel[target_name]
            source_syms = self._file_symbols.get(source_file, {})
            if target_name in source_syms:
                return ResolvedCall(caller_id, source_syms[target_name], 0.90, call.line)

        # 2b: Check all imported files for the symbol (pre-merged lookup)
        merged_syms = self._merged_symbols_for(file_path)
        if target_name in merged_syms:
            return ResolvedCall(caller_id, merged_syms[target_name], 0.85, call.line)

        # Tier 3: global unique match — only within the same language
        candidates = self._global_symbols.get(target_name, [])
        if len(candidates) == 1 and candidates[0] != caller_id:
            caller_lang = _file_language(self._parsed_files, caller_id)
            callee_lang = _file_language(self._parsed_files, candidates[0])
            if caller_lang and callee_lang and caller_lang != callee_lang:
                return None  # reject cross-language Tier 3 match
            return ResolvedCall(caller_id, candidates[0], 0.50, call.line)

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

        # Go: ``pkg.Func()`` where ``pkg`` is an import alias resolves to the
        # function in *any* file of that package, not just the single
        # representative the import resolved to. Try this first for Go so a
        # multi-file package's exported funcs are reached correctly.
        if self._is_go(file_path):
            go_pkg_call = self._resolve_go_package_call(file_path, call, caller_id)
            if go_pkg_call is not None:
                return go_pkg_call

        # JVM: receiver may be a class in the same package (no import needed)
        if self._is_jvm(file_path):
            index = self._get_jvm_index()
            if index is not None:
                siblings = index.same_package_files(file_path)
                for sibling in siblings:
                    methods = self._file_methods.get(sibling, {})
                    key = (receiver_name, method_name)
                    if key in methods:
                        return ResolvedCall(caller_id, methods[key], 0.90, call.line)
                    syms = self._file_symbols.get(sibling, {})
                    if receiver_name in syms:
                        # Found the class; look for the method on it
                        if key in methods:
                            return ResolvedCall(caller_id, methods[key], 0.88, call.line)

        # Strategy 1: receiver is a module alias (e.g. "import models" → "models.User()")
        module_file = self._module_aliases.get(file_path, {}).get(receiver_name)
        if module_file:
            source_syms = self._file_symbols.get(module_file, {})
            if method_name in source_syms:
                return ResolvedCall(caller_id, source_syms[method_name], 0.88, call.line)

        # Strategy 1b: receiver in import names (non-alias fallback for backward compat)
        name_to_file = self._import_names.get(file_path, {})
        if receiver_name in name_to_file and not module_file:
            source_file = name_to_file[receiver_name]
            source_syms = self._file_symbols.get(source_file, {})
            if method_name in source_syms:
                return ResolvedCall(caller_id, source_syms[method_name], 0.88, call.line)

        # Strategy 1c: Rust crate-scoped reference (e.g. typst_html::module)
        # The receiver is a crate name, the target is a symbol in that crate's lib.rs
        crate_src = self._get_rust_crate_src().get(receiver_name)
        if crate_src:
            for root_file in ("lib.rs", "main.rs"):
                crate_root = f"{crate_src}/{root_file}"
                root_syms = self._file_symbols.get(crate_root, {})
                if method_name in root_syms:
                    return ResolvedCall(caller_id, root_syms[method_name], 0.88, call.line)

        # Strategy 2: receiver is a known class name → look for method on that class
        # Check same-file classes first
        file_methods = self._file_methods.get(file_path, {})
        key = (receiver_name, method_name)
        if key in file_methods:
            return ResolvedCall(caller_id, file_methods[key], 0.93, call.line)

        # Check imported files for (class, method) pairs (pre-merged lookup)
        merged_methods = self._merged_methods_for(file_path)
        if key in merged_methods:
            return ResolvedCall(caller_id, merged_methods[key], 0.88, call.line)

        # Strategy 2b: trait method dispatch — receiver is a type that
        # implements a trait; the method may be defined on the trait's
        # impl block in another file. The global index preserves the old
        # file-insertion match order; entries from the caller's own file
        # are skipped exactly as before.
        for _path, sym_id in self._global_methods.get(key, ()):
            if _path == file_path:
                continue
            return ResolvedCall(caller_id, sym_id, 0.75, call.line)

        # Strategy 3: receiver is "self" or "this" — look in same class
        if receiver_name in ("self", "this"):
            # The caller is inside a class method — find its parent class
            # and resolve the method within that class's methods
            for path_key, methods in self._file_methods.items():
                if path_key != file_path:
                    continue
                for (cls_name, meth_name), sym_id in methods.items():
                    if meth_name == method_name and sym_id != caller_id:
                        # Verify caller is in the same class
                        caller_class = _extract_class_from_symbol_id(caller_id)
                        if caller_class and caller_class == cls_name:
                            return ResolvedCall(caller_id, sym_id, 0.95, call.line)

        # Strategy 4: global class match for receiver
        class_candidates = self._global_classes.get(receiver_name, [])
        if len(class_candidates) == 1:
            # Found unique class — look for the method in any file
            # that defines methods for this class
            for _path, methods in self._file_methods.items():
                if (receiver_name, method_name) in methods:
                    return ResolvedCall(
                        caller_id, methods[(receiver_name, method_name)], 0.50, call.line
                    )

        return None


def _extract_class_from_symbol_id(symbol_id: str) -> str | None:
    """Extract parent class name from a symbol ID like 'path::ClassName::method'."""
    parts = symbol_id.split("::")
    if len(parts) >= 3:
        return parts[-2]
    return None
