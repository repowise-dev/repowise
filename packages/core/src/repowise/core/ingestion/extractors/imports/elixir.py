"""Elixir ``alias`` / ``import`` / ``require`` / ``use`` directives."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..bindings.elixir import elixir_import_modules
from ..helpers import node_text
from ._types import ImportExtractor
from .generic import generic_statement


def _elixir_imports(
    stmt_node: Node, module_node: Node, src: str, seen_modules: set[str]
) -> list[Import]:
    """Elixir directives, one Import per module and deduped by module path.

    ``alias Foo.{Bar, Baz}`` names two modules in one statement. ``import``
    pulls in every public function (``*``); alias/require/use bind the module.
    """
    raw = node_text(stmt_node, src).split("\n", 1)[0].strip()
    directive_node = stmt_node.child_by_field_name("target")
    directive = node_text(directive_node, src).strip() if directive_node else ""
    imports: list[Import] = []
    for module_path in elixir_import_modules(module_node, src):
        if module_path in seen_modules:
            continue
        seen_modules.add(module_path)
        imports.append(
            Import(
                raw_statement=raw,
                module_path=module_path,
                imported_names=["*"] if directive == "import" else [],
                is_relative=False,
                resolved_file=None,
                bindings=[],
                is_reexport=False,
            )
        )
    return imports


ELIXIR_IMPORTS = ImportExtractor(statement=generic_statement, deduped=_elixir_imports)
