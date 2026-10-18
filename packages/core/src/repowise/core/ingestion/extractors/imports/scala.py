"""Scala import declarations."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import, NamedBinding
from ..bindings.scala import expand_scala_import_clauses
from ._types import ImportExtractor
from .generic import _generic_import


def _scala_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """Scala: one Import per selected name, with full dotted paths.

    The query's ``(identifier)`` capture is only the first path segment, and
    one declaration can hold several clauses, brace selectors, renames and
    wildcards.
    """
    imports: list[Import] = []
    for clause_path, clause_names in expand_scala_import_clauses(stmt_node, src):
        local = clause_names[0]
        exported = None if local == "*" else clause_path.rsplit(".", 1)[-1]
        imports.append(
            Import(
                raw_statement=raw,
                module_path=clause_path,
                imported_names=clause_names,
                is_relative=False,
                resolved_file=None,
                bindings=[
                    NamedBinding(
                        local_name=local,
                        exported_name=exported,
                        source_file=None,
                    )
                ],
                is_reexport=False,
            )
        )
    return imports


def _scala_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    if stmt_node.type == "import_declaration":
        return _scala_imports(stmt_node, raw, src)
    return [_generic_import(stmt_node, module_text, raw, language, src)]


SCALA_IMPORTS = ImportExtractor(statement=_scala_statement)
