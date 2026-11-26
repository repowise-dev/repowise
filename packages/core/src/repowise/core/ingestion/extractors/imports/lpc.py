"""Keep LPC include delimiters and inherit expressions intact for resolution."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ._types import ImportExtractor


def lpc_imports(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    # The shared parser strips quotes from module_text. That loses the
    # distinction between #include "relative.h" and #include SOME_MACRO.
    return [Import(
        raw_statement=raw,
        module_path=(
            raw if stmt_node.type == "inheritance"
            else "#include " + module_node.text.decode("utf-8", errors="replace")
        ),
        imported_names=[],
        is_relative=False,
        resolved_file=None,
    )]


LPC_IMPORTS = ImportExtractor(statement=lpc_imports)
