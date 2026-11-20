"""Project sourced Tcl procedures into the namespace of each source site."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import structlog
from tree_sitter import Node, Parser

from ..extractors.tcl import (
    _execution_namespace,
    is_tcl_switch_arm,
    tcl_leaf_name,
    tcl_proc_namespace,
)
from ..models import ParsedFile
from ..source_text import source_bytes

log = structlog.get_logger(__name__)

ProcKey = tuple[str | None, str]
ProcEntry = tuple[str, str]


@dataclass
class _FileSites:
    procs: list[tuple[Node, str, str]]
    sources: list[tuple[Node, str]]


class TclSourceIndex:
    """Cache syntax sites and their namespace projections for each sourcing file."""

    def __init__(
        self, parsed_files: dict[str, ParsedFile], source_map: dict[str, bytes] | None
    ) -> None:
        self._parsed_files = parsed_files
        self._source_map = source_map
        self._sites: dict[str, _FileSites] = {}
        self._projections: dict[str, tuple[set[str], dict[ProcKey, list[ProcEntry]]]] = {}
        self._contexts: dict[str, dict[str, set[str]]] = {}
        self._importers: dict[str, set[str]] | None = None
        self._execution_contexts: dict[str, list[tuple[str, str]]] = {}

    def project(self, path: str) -> tuple[set[str], dict[ProcKey, list[ProcEntry]]]:
        """Files reached by ``source`` and their procedures in each execution context."""
        cached = self._projections.get(path)
        if cached is not None:
            return cached
        reached: set[str] = set()
        procs: dict[ProcKey, list[ProcEntry]] = defaultdict(list)
        if path not in self._parsed_files or not self._source_targets(path):
            result = reached, {}
            self._projections[path] = result
            self._contexts[path] = {path: {""}}
            return result
        contexts: dict[str, set[str]] = defaultdict(set)
        pending: list[tuple[str, str, frozenset[str]]] = [(path, "", frozenset({path}))]
        visited: set[tuple[str, str]] = set()
        while pending:
            current, base, ancestors = pending.pop()
            if (current, base) in visited:
                continue
            visited.add((current, base))
            contexts[current].add(base)
            sites = self._file_sites(current)
            if current != path:
                reached.add(current)
                for node, raw, symbol_id in sites.procs:
                    namespace = tcl_proc_namespace(node, raw, base)
                    procs[(namespace, tcl_leaf_name(raw, ""))].append((current, symbol_id))
            for node, target in sites.sources:
                if target not in ancestors:
                    namespace = _execution_namespace(node.parent, base)
                    pending.append((target, namespace, ancestors | {target}))
        result = reached, dict(procs)
        self._projections[path] = result
        self._contexts[path] = dict(contexts)
        return result

    def execution_contexts(self, path: str) -> list[tuple[str, str]]:
        """Entry files and namespaces in which a sourced file can execute."""
        cached = self._execution_contexts.get(path)
        if cached is not None:
            return cached
        if self._importers is None:
            importers: dict[str, set[str]] = defaultdict(set)
            for source, parsed in self._parsed_files.items():
                if parsed.file_info.language == "tcl":
                    for target in self._source_targets(source).values():
                        importers[target].add(source)
            self._importers = dict(importers)
        if path not in self._importers:
            result = [(path, "")]
            self._execution_contexts[path] = result
            return result
        ancestors = {path}
        pending = [path]
        while pending:
            for importer in self._importers.get(pending.pop(), ()):
                if importer not in ancestors:
                    ancestors.add(importer)
                    pending.append(importer)
        roots = [source for source in ancestors if not self._importers.get(source)]
        if not roots:
            roots = [path]
        result: list[tuple[str, str]] = []
        for root in sorted(roots):
            self.project(root)
            result.extend(
                (root, base) for base in sorted(self._contexts.get(root, {}).get(path, ()))
            )
        if not result:
            log.warning("tcl_source_context_unavailable", path=path)
        self._execution_contexts[path] = result
        return result

    def proc_namespace(self, path: str, symbol_id: str, base: str) -> tuple[bool, str | None]:
        """A procedure's namespace when its file executes in *base*."""
        for node, raw, found_id in self._file_sites(path).procs:
            if found_id == symbol_id:
                return True, tcl_proc_namespace(node, raw, base)
        log.warning("tcl_source_proc_unavailable", path=path, symbol_id=symbol_id)
        return False, None

    def _source_targets(self, path: str) -> dict[str, str]:
        targets: dict[str, str] = {}
        for imp in self._parsed_files[path].imports:
            target = imp.resolved_file
            if (
                imp.raw_statement.split(None, 1)[:1] == ["source"]
                and target is not None
                and target in self._parsed_files
                and self._parsed_files[target].file_info.language == "tcl"
            ):
                targets[imp.raw_statement] = target
        return targets

    def _file_sites(self, path: str) -> _FileSites:
        cached = self._sites.get(path)
        if cached is not None:
            return cached
        parsed = self._parsed_files[path]
        data = source_bytes(path, parsed.file_info.abs_path, self._source_map)
        if not data and parsed.file_info.size_bytes:
            log.warning("tcl_source_context_unavailable", path=path)
        from ..parser.grammars import _get_language

        language = _get_language("tcl")
        if language is None:
            raise RuntimeError("Tcl grammar unavailable while resolving source namespaces")
        root = Parser(language).parse(data).root_node
        symbols: dict[tuple[int, str, str | None], list[str]] = defaultdict(list)
        for symbol in parsed.symbols:
            symbols[(symbol.start_line, symbol.name, symbol.parent_name)].append(symbol.id)
        targets = self._source_targets(path)
        procs: list[tuple[Node, str, str]] = []
        sources: list[tuple[Node, str]] = []
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            if node.type == "procedure" and not is_tcl_switch_arm(node):
                name = node.child_by_field_name("name")
                if name is None:
                    continue
                raw = (name.text or b"").decode("utf-8", "replace")
                key = (
                    node.start_point.row + 1,
                    tcl_leaf_name(raw, ""),
                    tcl_proc_namespace(node, raw),
                )
                ids = symbols.get(key, [])
                if len(ids) == 1:
                    procs.append((node, raw, ids[0]))
            elif node.type == "command":
                head = node.child_by_field_name("name")
                if head is not None and head.text == b"source" and not is_tcl_switch_arm(node):
                    raw = (node.text or b"").decode("utf-8", "replace").strip()
                    target = targets.get(raw)
                    if target is not None:
                        sources.append((node, target))
        sites = _FileSites(procs, sources)
        self._sites[path] = sites
        return sites
