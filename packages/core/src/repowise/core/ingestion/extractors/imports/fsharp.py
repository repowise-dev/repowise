"""F# ``open`` declarations."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..helpers import node_text
from ._types import ImportExtractor
from .generic import generic_statement


def _fsharp_imports(
    stmt_node: Node, module_node: Node, src: str, seen_raws: set[str]
) -> list[Import]:
    """F# ``open``: every public name of the module (``*``), which bare-name
    call resolution reads. ``open type A.B.T`` binds the type's static members,
    so the module the file depends on is ``A.B``.
    """
    raw = node_text(stmt_node, src).strip()
    if raw in seen_raws:
        return []
    seen_raws.add(raw)
    module_path = node_text(module_node, src).strip()
    if not module_path:
        return []
    names: list[str] = ["*"]
    if any(child.type == "type" for child in stmt_node.children):
        head, _, type_name = module_path.rpartition(".")
        if head:
            module_path, names = head, [type_name]
        else:
            names = []
    return [
        Import(
            raw_statement=raw,
            module_path=module_path,
            imported_names=names,
            is_relative=False,
            resolved_file=None,
            bindings=[],
            is_reexport=False,
        )
    ]


FSHARP_IMPORTS = ImportExtractor(statement=generic_statement, deduped=_fsharp_imports)
