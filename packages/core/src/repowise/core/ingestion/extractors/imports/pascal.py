"""Pascal ``uses`` clauses."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..helpers import node_text
from ._types import ImportExtractor
from .generic import generic_statement


def _pascal_imports(
    stmt_node: Node, module_node: Node, src: str, seen_units: set[str]
) -> list[Import]:
    """One Pascal ``uses`` unit, deduped case-insensitively across the file.

    pascal.scm fires once per unit name, so a 3-unit clause arrives as 3
    matches sharing one statement span: deduping by raw text would drop all
    but the first. A unit named in both ``uses`` clauses is one dependency.
    ``uses`` exposes the unit's whole interface, hence the ``*`` wildcard.
    """
    raw = node_text(stmt_node, src).strip()
    unit_name = node_text(module_node, src).strip()
    if not unit_name or unit_name.lower() in seen_units:
        return []
    seen_units.add(unit_name.lower())
    return [
        Import(
            raw_statement=raw,
            module_path=unit_name,
            imported_names=["*"],
            is_relative=False,
            resolved_file=None,
            bindings=[],
            is_reexport=False,
        )
    ]


PASCAL_IMPORTS = ImportExtractor(statement=generic_statement, deduped=_pascal_imports)
