"""Python imports."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..bindings.python import expand_bare_relative_imports
from ._types import ImportExtractor
from .generic import generic_statement


def _python_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    """The Import of one match; ``import a, b`` matches once per name.

    Each match of a multi-name ``import`` keeps only its own name and binding,
    so every module gets its own edge.
    """
    imports = generic_statement(stmt_node, module_node, module_text, raw, language, src)
    if stmt_node.type != "import_statement":
        return imports
    names = stmt_node.children_by_field_name("name")
    if len(names) < 2:
        return imports
    parent = module_node.parent
    own = parent if parent is not None and parent.type == "aliased_import" else module_node
    index = next((i for i, name in enumerate(names) if name.start_byte == own.start_byte), None)
    imp = imports[0]
    # ``import`` yields one name and one binding per ``name`` child, in order.
    if index is None or len(imp.imported_names) != len(names) or len(imp.bindings) != len(names):
        return imports
    imp.imported_names = [imp.imported_names[index]]
    imp.bindings = [imp.bindings[index]]
    return imports


PYTHON_IMPORTS = ImportExtractor(
    statement=_python_statement, finalize=expand_bare_relative_imports, per_module=True
)
