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

import structlog

from .models import CallSite, ParsedFile

log = structlog.get_logger(__name__)


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
    ) -> None:
        # Per-file symbol index: {file_path: {symbol_name: symbol_id}}
        self._file_symbols: dict[str, dict[str, str]] = {}

        # Per-file method index: {file_path: {(class_name, method_name): symbol_id}}
        self._file_methods: dict[str, dict[tuple[str, str], str]] = {}

        # Global symbol index: {name: [symbol_ids]} — for Tier 3
        self._global_symbols: dict[str, list[str]] = defaultdict(list)

        # Global class index: {name: symbol_id} — for receiver resolution
        self._global_classes: dict[str, list[str]] = defaultdict(list)

        # Import graph: {file_path: set of imported file paths}
        self._import_targets = import_targets

        # Import name mapping: {file_path: {imported_name: source_file}}
        self._import_names: dict[str, dict[str, str]] = defaultdict(dict)

        self._build_indices(parsed_files)

    def _build_indices(self, parsed_files: dict[str, ParsedFile]) -> None:
        """Build all lookup indices from parsed file data."""
        for path, parsed in parsed_files.items():
            file_syms: dict[str, str] = {}
            file_methods: dict[tuple[str, str], str] = {}

            for sym in parsed.symbols:
                # File-level symbol index (top-level symbols and methods)
                file_syms[sym.name] = sym.id

                # Method index: (class_name, method_name) → symbol_id
                if sym.parent_name:
                    file_methods[(sym.parent_name, sym.name)] = sym.id

                # Global indices
                self._global_symbols[sym.name].append(sym.id)
                if sym.kind in ("class", "struct", "interface", "enum"):
                    self._global_classes[sym.name].append(sym.id)

            self._file_symbols[path] = file_syms
            self._file_methods[path] = file_methods

            # Build import-name mapping from raw import data
            for imp in parsed.imports:
                # Determine target file for this import
                targets = self._import_targets.get(path, set())
                for target_file in targets:
                    for name in imp.imported_names:
                        if name == "*":
                            # Wildcard import — all symbols from target are accessible
                            continue
                        self._import_names[path][name] = target_file

    def resolve_file(self, file_path: str, calls: list[CallSite]) -> list[ResolvedCall]:
        """Resolve all calls in a single file to symbol-level edges."""
        results: list[ResolvedCall] = []

        for call in calls:
            if not call.caller_symbol_id:
                # Calls at module level (outside any function) — skip for now
                # as they don't have a clear caller node
                continue

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

        # Tier 2: import-scoped
        # 2a: Check specific imported name → source file
        name_to_file = self._import_names.get(file_path, {})
        if target_name in name_to_file:
            source_file = name_to_file[target_name]
            source_syms = self._file_symbols.get(source_file, {})
            if target_name in source_syms:
                return ResolvedCall(caller_id, source_syms[target_name], 0.90, call.line)

        # 2b: Check all imported files for the symbol
        for imported_file in self._import_targets.get(file_path, set()):
            if imported_file.startswith("external:"):
                continue
            imported_syms = self._file_symbols.get(imported_file, {})
            if target_name in imported_syms:
                return ResolvedCall(caller_id, imported_syms[target_name], 0.85, call.line)

        # Tier 3: global unique match
        candidates = self._global_symbols.get(target_name, [])
        if len(candidates) == 1 and candidates[0] != caller_id:
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

        # Strategy 1: receiver matches an imported name that resolves to a file
        # (module-alias pattern: e.g. Python "import models" then "models.User()")
        name_to_file = self._import_names.get(file_path, {})
        if receiver_name in name_to_file:
            source_file = name_to_file[receiver_name]
            source_syms = self._file_symbols.get(source_file, {})
            if method_name in source_syms:
                return ResolvedCall(caller_id, source_syms[method_name], 0.88, call.line)

        # Strategy 2: receiver is a known class name → look for method on that class
        # Check same-file classes first
        file_methods = self._file_methods.get(file_path, {})
        key = (receiver_name, method_name)
        if key in file_methods:
            return ResolvedCall(caller_id, file_methods[key], 0.93, call.line)

        # Check imported files for (class, method) pairs
        for imported_file in self._import_targets.get(file_path, set()):
            if imported_file.startswith("external:"):
                continue
            imp_methods = self._file_methods.get(imported_file, {})
            if key in imp_methods:
                return ResolvedCall(caller_id, imp_methods[key], 0.88, call.line)

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
