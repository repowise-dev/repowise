"""Dart ``import`` / ``export`` / ``part`` directives."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..bindings import extract_import_bindings
from ._types import ImportExtractor


def _dart_import(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, src: str
) -> Import:
    """Dart: URIs are relative unless schemed, ``export`` re-exports, and the
    dotted ``part of library.name;`` form resolves through ``library:``.
    """
    module_path = module_text
    if module_node.type == "dotted_identifier_list":
        module_path = f"library:{module_text}"
    imported_names, bindings = extract_import_bindings(stmt_node, src, "dart")
    return Import(
        raw_statement=raw,
        module_path=module_path,
        imported_names=imported_names,
        is_relative=not module_path.startswith(("package:", "dart:", "library:")),
        resolved_file=None,
        bindings=bindings,
        is_reexport=stmt_node.type == "library_export",
    )


def _dart_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    return [_dart_import(stmt_node, module_node, module_text, raw, src)]


DART_IMPORTS = ImportExtractor(statement=_dart_statement)
