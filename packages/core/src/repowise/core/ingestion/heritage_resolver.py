"""Heritage (inheritance/implementation) resolution engine.

Resolves HeritageRelation objects (extracted from AST class definitions) to
concrete symbol node IDs in the graph, producing EXTENDS, IMPLEMENTS, and
TRAIT_IMPL edges with confidence scores.

Resolution tiers (checked in order, first match wins):

    Tier 1 — Same-file exact match (confidence 0.95)
        The parent name matches a class/interface/trait in the same file.

    Tier 2 — Import-scoped match (confidence 0.90)
        The parent name matches a class/interface/trait in an imported file.

    Tier 3 — Global unique match (confidence 0.50)
        The parent name matches exactly one class/interface/trait globally.

Each resolved relation produces a (child_id, parent_id, edge_type, confidence)
tuple that the GraphBuilder converts into graph edges.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import structlog

from .models import HeritageRelation, ParsedFile

log = structlog.get_logger(__name__)


def _file_language(parsed_files: dict[str, ParsedFile], symbol_id: str) -> str | None:
    """Extract language from a symbol ID's file via the parsed files map."""
    file_path = symbol_id.split("::")[0] if "::" in symbol_id else symbol_id
    parsed = parsed_files.get(file_path)
    return parsed.file_info.language if parsed else None


# Symbol kinds that can be parents in heritage relationships
_PARENT_KINDS = frozenset(
    {
        "class",
        "interface",
        "trait",
        "struct",
        "enum",
        "type_alias",
        "impl",
    }
)


@dataclass(frozen=True, slots=True)
class ResolvedHeritage:
    """A heritage relation resolved to concrete symbol IDs."""

    child_id: str  # symbol node ID of the child class/struct
    parent_id: str  # symbol node ID of the parent class/interface/trait
    edge_type: str  # "extends", "implements", or "trait_impl"
    confidence: float  # 0.0–1.0
    line: int  # line of the class definition (for diagnostics)


class HeritageResolver:
    """Resolve raw HeritageRelations to symbol-level edges.

    Constructed once per ``GraphBuilder.build()`` call with the full set
    of parsed files and import edges. Follows the same architecture as
    CallResolver for consistency.
    """

    def __init__(
        self,
        parsed_files: dict[str, ParsedFile],
        import_targets: dict[str, set[str]],
    ) -> None:
        # Per-file class/interface/trait index: {file: {name: symbol_id}}
        self._file_types: dict[str, dict[str, str]] = {}

        # Global class/interface/trait index: {name: [symbol_ids]}
        self._global_types: dict[str, list[str]] = defaultdict(list)

        # Import graph
        self._import_targets = import_targets

        # Import name mapping (reuses resolved_file from Import objects)
        self._import_names: dict[str, dict[str, str]] = defaultdict(dict)

        # Keep reference for cross-language checks in Tier 3
        self._parsed_files = parsed_files

        self._build_indices(parsed_files)

    def _build_indices(self, parsed_files: dict[str, ParsedFile]) -> None:
        """Build type-level lookup indices from parsed file data."""
        for path, parsed in parsed_files.items():
            file_types: dict[str, str] = {}

            for sym in parsed.symbols:
                if sym.kind in _PARENT_KINDS:
                    file_types[sym.name] = sym.id
                    self._global_types[sym.name].append(sym.id)

            self._file_types[path] = file_types

            # Build import name mapping using resolved_file
            for imp in parsed.imports:
                if imp.resolved_file is None:
                    continue
                for binding in imp.bindings:
                    if binding.local_name != "*":
                        self._import_names[path][binding.local_name] = imp.resolved_file
                # Fallback for imports without bindings
                if not imp.bindings:
                    for name in imp.imported_names:
                        if name != "*":
                            self._import_names[path][name] = imp.resolved_file

    def resolve_file(
        self, file_path: str, relations: list[HeritageRelation]
    ) -> list[ResolvedHeritage]:
        """Resolve all heritage relations in a single file."""
        results: list[ResolvedHeritage] = []

        for rel in relations:
            resolved = self._resolve_one(file_path, rel)
            if resolved:
                results.append(resolved)

        return results

    def _resolve_one(self, file_path: str, rel: HeritageRelation) -> ResolvedHeritage | None:
        """Resolve a single HeritageRelation through three-tier fallback."""
        child_id = f"{file_path}::{rel.child_name}"
        parent_name = rel.parent_name
        edge_type = _heritage_kind_to_edge_type(rel.kind)

        # Tier 1: same-file
        file_types = self._file_types.get(file_path, {})
        if parent_name in file_types:
            return ResolvedHeritage(
                child_id=child_id,
                parent_id=file_types[parent_name],
                edge_type=edge_type,
                confidence=0.95,
                line=rel.line,
            )

        # Tier 2a: specific imported name
        name_to_file = self._import_names.get(file_path, {})
        if parent_name in name_to_file:
            source_file = name_to_file[parent_name]
            source_types = self._file_types.get(source_file, {})
            if parent_name in source_types:
                return ResolvedHeritage(
                    child_id=child_id,
                    parent_id=source_types[parent_name],
                    edge_type=edge_type,
                    confidence=0.90,
                    line=rel.line,
                )

        # Tier 2b: scan all imported files
        for imported_file in self._import_targets.get(file_path, set()):
            if imported_file.startswith("external:"):
                continue
            imported_types = self._file_types.get(imported_file, {})
            if parent_name in imported_types:
                return ResolvedHeritage(
                    child_id=child_id,
                    parent_id=imported_types[parent_name],
                    edge_type=edge_type,
                    confidence=0.85,
                    line=rel.line,
                )

        # Tier 3: global unique match — only within the same language
        candidates = self._global_types.get(parent_name, [])
        if len(candidates) == 1:
            caller_lang = _file_language(self._parsed_files, child_id)
            callee_lang = _file_language(self._parsed_files, candidates[0])
            if caller_lang and callee_lang and caller_lang != callee_lang:
                return None  # reject cross-language Tier 3 match
            return ResolvedHeritage(
                child_id=child_id,
                parent_id=candidates[0],
                edge_type=edge_type,
                confidence=0.50,
                line=rel.line,
            )

        return None


def _heritage_kind_to_edge_type(kind: str) -> str:
    """Map HeritageKind to graph edge_type string."""
    if kind == "implements":
        return "implements"
    if kind == "trait_impl":
        return "implements"
    # extends, mixin → extends
    return "extends"
