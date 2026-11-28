"""Java and Kotlin imports."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ._types import ImportExtractor
from .generic import _generic_import


def _jvm_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    # The query captures the scoped identifier only, so ``import com.foo.*``
    # arrives as ``com.foo``. Restore the ``.*``.
    if not module_text.endswith("*"):
        stmt_text = raw.rstrip().rstrip(";").rstrip()
        if stmt_text.endswith(".*"):
            module_text += ".*"
    return [_generic_import(stmt_node, module_text, raw, language, src)]


JVM_IMPORTS = ImportExtractor(statement=_jvm_statement)
