"""Swift imports."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ._types import ImportExtractor
from .generic import _generic_import


def _swift_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    # ``@_exported import`` re-exports the module.
    is_reexport = raw.startswith("@_exported")
    return [_generic_import(stmt_node, module_text, raw, language, src, is_reexport=is_reexport)]


SWIFT_IMPORTS = ImportExtractor(statement=_swift_statement)
