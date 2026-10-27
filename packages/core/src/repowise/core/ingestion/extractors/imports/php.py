"""PHP ``use`` declarations."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import, NamedBinding
from ..bindings.php import php_class_name, php_use_clauses
from ._types import ImportExtractor
from .generic import _generic_import


def _php_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """PHP: one ``use`` declaration can name several classes, each its own file."""
    return [
        Import(
            raw_statement=raw,
            module_path=fqn,
            imported_names=[php_class_name(fqn)],
            is_relative=False,
            resolved_file=None,
            bindings=[NamedBinding(local_name=local, exported_name=fqn, source_file=None)],
            is_reexport=False,
        )
        for fqn, local in php_use_clauses(stmt_node, src)
    ]


def _php_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    if stmt_node.type == "namespace_use_declaration":
        return _php_imports(stmt_node, raw, src)
    return [_generic_import(stmt_node, module_text, raw, language, src)]


PHP_IMPORTS = ImportExtractor(statement=_php_statement)
