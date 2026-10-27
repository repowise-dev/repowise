"""The unused-export pass: public symbols no importer, edge or framework reaches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ....entry_candidacy import is_reachability_root
from ..apex_reachability import is_apex_entry_member
from ..constants import (
    _CONTAINER_USE_LANGUAGES,
    _DEAD_CODE_EXEMPT_LANGUAGES,
    _PREPROCESSED_LANGUAGES,
    _is_fixture_path,
)
from ..contract_methods import is_com_method_implementation, is_contract_method
from ..decorator_rules import (
    _declared_deliberately_unused,
    _is_framework_registered,
    _is_symbol_deprecated,
)
from ..finding_helpers import _is_synthetic_node, _symbol_span
from ..jvm_reachability import defines_main
from ..models import DeadCodeFindingData, DeadCodeKind
from ..risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    UNPROVEN_EXPORT_CONFIDENCE,
    path_risk_factors,
    risk_evidence,
)
from ..symbol_rules import (
    _ENTRY_POINT_SYMBOL_NAMES,
    _is_compiler_invoked,
    _is_declaration_only,
    _nested_class_used,
    _non_importable_kinds,
)
from ..vbnet_modules import vbnet_module_names

if TYPE_CHECKING:
    from ..analyzer import DeadCodeAnalyzer


@dataclass
class _ExportFile:
    """One file's facts, shared by every public symbol the export pass judges in it."""

    node: Any
    node_data: dict
    symbol_pairs: list[tuple[str, dict]]
    # An importer names a symbol this file defines, so a name missing from
    # every importer means something. A C ``#include`` names nothing and a C#
    # ``using`` names a namespace, so neither says anything about a symbol.
    importers_name_symbols: bool
    # Classes holding C# extension methods, called as ``x.Method()`` without
    # naming the class, and VB.NET Modules, whose members are in scope
    # unqualified: no importer would name either.
    extension_containers: frozenset[str]
    imported_as_namespace: bool
    enclosing_ranges: list[tuple[int, int]]


class UnusedExportPass:
    """Judges every public top-level symbol of every eligible file."""

    def __init__(self, ctx: DeadCodeAnalyzer) -> None:
        self.ctx = ctx
        self.graph = ctx.graph

    def run(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect public symbols with no incoming edges."""
        findings = []

        for node in self.graph.nodes():
            file_ctx = self._export_file_context(node, whitelist)
            if file_ctx is None:
                continue
            for sym_id, sym in file_ctx.symbol_pairs:
                if sym.get("visibility") != "public":
                    continue
                if not self._is_export_candidate(file_ctx, sym, dynamic_patterns):
                    continue
                rescued, unsatisfied_guard = self._export_use_evidence(file_ctx, sym_id, sym)
                if rescued:
                    continue
                findings.append(self._make_unused_export_finding(file_ctx, sym, unsatisfied_guard))

        return findings

    def _member_is_used(self, sym_id: str, language: str | None) -> bool:
        """True when this container declares a method something else uses.

        Only a ``calls`` edge from outside the container counts: the class's own
        methods calling each other prove nothing, and an ``implements`` edge
        says the member fits a contract, not that anyone reaches the class.
        """
        if language not in _CONTAINER_USE_LANGUAGES or not self.graph.has_node(sym_id):
            return False
        members = {
            method_id
            for _, method_id, data in self.graph.out_edges(sym_id, data=True)
            if data.get("edge_type") == "has_method"
        }
        within = members | {sym_id}
        for method_id in members:
            if any(
                pred not in within and self.graph[pred][method_id].get("edge_type") == "calls"
                for pred in self.graph.predecessors(method_id)
            ):
                return True
        return False

    def _export_file_context(self, node: Any, whitelist: set[str]) -> _ExportFile | None:
        """The file-level facts the unused-export pass needs, or None to skip the file."""
        if _is_synthetic_node(str(node)):
            return None

        node_data = self.graph.nodes[node]
        if node_data.get("language", "unknown") in _DEAD_CODE_EXEMPT_LANGUAGES:
            return None
        # Roots include framework-instantiated files the runtime constructs.
        # Not ``is_file_reachable``: its barrel rescue is scoped to files, and an
        # unused symbol defined in a barrel should still be flagged.
        # A JVM ``main`` launches its file the same way.
        if is_reachability_root(node_data) or defines_main(self.graph, str(node)):
            return None
        if node_data.get("is_test", False):
            return None
        if _is_fixture_path(str(node)):
            return None
        if self.ctx._should_never_flag(str(node), whitelist):
            return None

        symbol_pairs = self.ctx._defined_symbols(node)
        if not symbol_pairs:
            return None

        if self._runtime_reaches_every_member(node):
            return None

        # A bundler ``resolve.alias`` target stands in for a whole package.
        if str(node) in self.ctx._bundler_alias_targets:
            return None

        symbols = [sym for _, sym in symbol_pairs]
        return _ExportFile(
            node=node,
            node_data=node_data,
            symbol_pairs=symbol_pairs,
            importers_name_symbols=self._importers_name_symbols(node, symbols),
            extension_containers=self._extension_containers(node, node_data, symbol_pairs),
            imported_as_namespace=self._imported_as_namespace(node, node_data),
            # Function bodies, so nested defs (closures) can be skipped.
            enclosing_ranges=[
                (sym.get("start_line", 0), sym.get("end_line", 0))
                for sym in symbols
                if sym.get("kind") in ("function", "method", "async_function")
                and sym.get("end_line", 0) > sym.get("start_line", 0)
            ],
        )

    def _runtime_reaches_every_member(self, node: Any) -> bool:
        """Whether a whole-module dynamic-use or framework edge lands on *node*.

        Not ``dynamic_imports`` (a loaded module, which a plain import does not
        rescue either), nor a framework edge naming its exports (a Lambda
        handler), which the importer check rescues one name at a time.
        """
        return any(
            (edge.get("edge_type") in ("dynamic_uses", "framework") and not edge.get("imported_names"))
            or edge.get("dynamic_hint")
            for edge in (
                self.graph.get_edge_data(pred, node, {}) for pred in self.graph.predecessors(node)
            )
        )

    def _importers_name_symbols(self, node: Any, symbols: list[dict]) -> bool:
        """Whether any edge into *node* names a symbol the file defines."""
        defined = {sym.get("name") for sym in symbols}
        return any(
            not defined.isdisjoint(imported)
            for imported in self.ctx._imported_names_by_edge(node, edge_types=None)
        )

    def _extension_containers(
        self, node: Any, node_data: dict, symbol_pairs: list[tuple[str, dict]]
    ) -> frozenset[str]:
        """Containers here a use never names: C# extension holders, VB.NET Modules."""
        if node_data.get("language") not in _CONTAINER_USE_LANGUAGES:
            return frozenset()
        modules = vbnet_module_names(
            (sym for _, sym in symbol_pairs), self.ctx._source_map.get(str(node))
        )
        return modules | frozenset(
            sym.get("name", "")
            for sym_id, sym in symbol_pairs
            if self._declares_extension_method(sym_id)
        )

    def _imported_as_namespace(self, node: Any, node_data: dict) -> bool:
        """Whether an importer pulled this file by its module name.

        Then any public symbol may be reached as ``mod.<attr>``. Not for Go,
        where every import names a package and package calls resolve precisely.
        """
        file_stem = Path(str(node)).stem
        if (
            not file_stem
            or file_stem in ("__init__", "index")
            or node_data.get("language") == "go"
        ):
            return False
        return any(file_stem in imported for imported in self.ctx._imported_names_by_edge(node))

    def _is_export_candidate(
        self, file_ctx: _ExportFile, sym: dict, dynamic_patterns: tuple[str, ...]
    ) -> bool:
        """Whether a public symbol is the kind of thing the pass may report."""
        if not self._is_importable_by_name(file_ctx, sym):
            return False
        return not self._used_without_an_edge(file_ctx.node_data, sym, dynamic_patterns)

    def _is_importable_by_name(self, file_ctx: _ExportFile, sym: dict) -> bool:
        """False for symbols no importer can name: members, intrinsics, declarations."""
        node = file_ctx.node
        sym_name = sym.get("name", "")
        # Members are reached through their container, never imported by name.
        if sym.get("kind") in _non_importable_kinds(sym.get("language", "unknown")):
            return False
        # Types in a ``namespace JSX`` file are used implicitly by JSX expressions.
        if (
            sym.get("kind") in ("interface", "type_alias")
            and str(node) in self.ctx._jsx_namespace_files
        ):
            return False
        if sym_name.startswith("__") and sym_name.endswith("__"):
            return False
        if sym_name in _ENTRY_POINT_SYMBOL_NAMES:
            return False
        # VS Code calls ``activate``/``deactivate`` on the extension module.
        if sym_name in ("activate", "deactivate") and Path(str(node)).stem == "extension":
            return False
        if _is_compiler_invoked(sym, sym_name):
            return False
        # A COM interface method the runtime calls through the vtable.
        if is_com_method_implementation(sym.get("signature"), sym.get("language")):
            return False
        if _is_declaration_only(sym):
            return False
        # Dotted names are namespace path fragments, not exports.
        if "." in sym_name:
            return False
        # A def nested in another function's body cannot be imported.
        sym_start = sym.get("start_line", 0)
        return not any(
            start < sym_start < end
            for start, end in file_ctx.enclosing_ranges
            if (start, end) != (sym_start, sym.get("end_line", 0))
        )

    def _used_without_an_edge(
        self, node_data: dict, sym: dict, dynamic_patterns: tuple[str, ...]
    ) -> bool:
        """Uses the graph cannot carry: framework registration, dynamic names, local refs."""
        sym_name = sym.get("name", "")
        decorators = sym.get("decorators", [])

        meta = self.ctx._framework_meta_annotations(sym.get("file_path", ""))
        if _is_framework_registered(decorators, meta):
            return True
        # A ``global`` nested Apex class is reached by subscribers and ``Type.forName``.
        if is_apex_entry_member(sym):
            return True
        if _declared_deliberately_unused(decorators):
            return True

        if self.ctx._name_matches_dynamic(sym_name, dynamic_patterns):
            return True

        if _nested_class_used(node_data, sym):
            return True

        # TS/JS: type names referenced in type positions of the same file.
        local_type_uses = node_data.get("local_type_uses")
        if local_type_uses and sym_name in local_type_uses:
            return True

        # Python: same-module references in non-call positions (callables passed
        # as values, annotations, decorators), see ``ingestion/python_local_refs.py``.
        local_refs = node_data.get("local_refs")
        return bool(local_refs and sym_name in local_refs)

    def _export_use_evidence(
        self, file_ctx: _ExportFile, sym_id: str, sym: dict
    ) -> tuple[bool, tuple[str, str] | None]:
        """``(rescued, unsatisfied_guard)`` from importers and symbol-level edges.

        A symbol with importers is rescued unless every import renders it
        behind a JSX prop guard nobody supplies; that guard is returned so the
        finding can say so.
        """
        node = file_ctx.node
        sym_name = sym.get("name", "")
        # ``export { local as alias }`` publishes the symbol under the
        # alias; importers carry the alias in ``imported_names``.
        export_alias = self.ctx._ts_export_aliases.get(str(node), {}).get(sym_name)
        has_importers = any(
            sym_name in imported_names
            or "*" in imported_names
            or (export_alias is not None and export_alias in imported_names)
            for imported_names in (
                self.graph[pred][node].get("imported_names", [])
                for pred in self.graph.predecessors(node)
            )
        )

        unsatisfied_guard: tuple[str, str] | None = None
        if has_importers:
            if file_ctx.node_data.get("language", "") in ("typescript", "javascript"):
                unsatisfied_guard = self.ctx._get_unsatisfied_prop_guard(sym_id, sym_name)
            if unsatisfied_guard is None:
                return True, None

        # Namespace-import rescue: see ``_imported_as_namespace``. Any public
        # symbol in a file pulled by module name could be the dispatch target
        # for ``<modname>.<attr>(...)``.
        if file_ctx.imported_as_namespace and unsatisfied_guard is None:
            return True, None

        # Any symbol-level use edge counts even when ``imported_names`` missed
        # it; a use of one overload uses the set.
        if unsatisfied_guard is None and self.ctx._has_inbound_use(sym_id):
            return True, None

        if self._member_is_used(sym_id, sym.get("language")):
            return True, None
        return False, unsatisfied_guard

    def _make_unused_export_finding(
        self,
        file_ctx: _ExportFile,
        sym: dict,
        unsatisfied_guard: tuple[str, str] | None,
    ) -> DeadCodeFindingData:
        node = file_ctx.node
        sym_name = sym.get("name", "")
        risk_factors = path_risk_factors(str(node))

        if unsatisfied_guard:
            guard_prop, caller_name = unsatisfied_guard
            confidence = RISK_CAP_CONFIDENCE
            if risk_factors:
                confidence = min(confidence, RISK_CAP_CONFIDENCE)
            safe = False
            reason = f"Public symbol '{sym_name}' is rendered behind prop guard '{guard_prop}' which is never supplied"
            evidence = [f"Prop '{guard_prop}' is missing across all call sites of '{caller_name}'"]
        else:
            confidence = self._unused_export_confidence(file_ctx, sym, risk_factors)
            safe = confidence >= SAFE_CONFIDENCE_THRESHOLD

            evidence = [f"No file imports symbol '{sym_name}' from {Path(node).name}"]
            risk_line = risk_evidence(risk_factors)
            if risk_line:
                evidence.append(risk_line)
            if self.ctx._dynamic_import_files and confidence <= RISK_CAP_CONFIDENCE:
                evidence.append(
                    "Package uses dynamic imports or runtime-resolved edges, "
                    "so the absence of a static reference does not establish disuse"
                )
            reason = f"Public symbol '{sym_name}' has no importers"

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNUSED_EXPORT,
            file_path=str(node),
            symbol_name=sym_name,
            symbol_kind=sym.get("kind"),
            confidence=confidence,
            reason=reason,
            evidence=evidence,
            safe_to_delete=safe,
            risk_factors=list(risk_factors),
            **_symbol_span(sym),
            **self.ctx._git_fields(str(node)),
        )

    @staticmethod
    def _import_absence_is_evidence(file_ctx: _ExportFile, sym: dict) -> bool:
        """Whether a use of *sym* would have shown up as an importer naming it.

        Not when no importer names this file's symbols; not where the
        preprocessor reaches a symbol through macros, typedef aliases and token
        pasting; not for a C# extension holder or a VB.NET Module.
        """
        return (
            file_ctx.importers_name_symbols
            and sym.get("language") not in _PREPROCESSED_LANGUAGES
            and sym.get("name") not in file_ctx.extension_containers
        )

    def _declares_extension_method(self, sym_id: str) -> bool:
        """Whether this container declares a C# extension method (``this T x``)."""
        return any(
            data.get("edge_type") == "has_method"
            and "(this " in (self.graph.nodes[method_id].get("signature") or "")
            for _, method_id, data in self.graph.out_edges(sym_id, data=True)
        )

    def _unused_export_confidence(
        self, file_ctx: _ExportFile, sym: dict, risk_factors: Any
    ) -> float:
        """Confidence for an export no importer reaches, capped by each doubt."""
        node = file_ctx.node
        sym_name = sym.get("name", "")
        if _is_symbol_deprecated(sym_name, sym.get("decorators") or []):
            confidence = 0.3
        elif self._import_absence_is_evidence(file_ctx, sym):
            confidence = 1.0
        else:
            # Ranked, never deletion-ready.
            confidence = UNPROVEN_EXPORT_CONFIDENCE

        if sym.get("kind") == "interface" and not self._file_has_implementors(node):
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        if is_contract_method(
            sym_name,
            sym.get("kind"),
            sym.get("language", "unknown"),
            modifiers=sym.get("modifiers"),
        ):
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        if risk_factors:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        if self.ctx._dynamic_import_dirs and str(Path(node).parent) in self.ctx._dynamic_import_dirs:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)
        return confidence

    def _file_has_implementors(self, file_node: Any) -> bool:
        """Whether an implements/extends edge lands on *file_node* or a symbol it defines.

        Implementor resolution varies by language, so an interface with none
        observed is capped as missing signal rather than read as dead.
        """
        implementor_edges = ("implements", "method_implements", "extends")
        # File-level incoming edges (XAML bindings, framework edges)
        for pred in self.graph.predecessors(file_node):
            if self.graph[pred][file_node].get("edge_type") in implementor_edges:
                return True
        # Interfaces usually receive ``implements`` on the type symbol.
        for succ in self.graph.successors(file_node):
            succ_data = self.graph.nodes.get(succ, {})
            if succ_data.get("node_type") != "symbol":
                continue
            for pred in self.graph.predecessors(succ):
                if self.graph[pred][succ].get("edge_type") in implementor_edges:
                    return True
        return False
