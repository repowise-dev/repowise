"""The import of a statement no language-specific shape claims."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import
from ..bindings import extract_import_bindings
from ._types import ImportExtractor


def _generic_import(
    stmt_node: Node,
    module_text: str,
    raw: str,
    language: str,
    src: str,
    *,
    is_reexport: bool = False,
    is_module_declaration: bool = False,
) -> Import:
    """The single Import of a statement no language-specific shape claims."""
    imported_names, bindings = extract_import_bindings(stmt_node, src, language)
    is_relative = (
        module_text.startswith(".")
        or module_text.startswith("./")
        or module_text.startswith(("self::", "super::", "crate::"))
    )
    # ``type_only`` / ``deferred`` are set by the caller from ``import_load_kind``.
    return Import(
        raw_statement=raw,
        module_path=module_text,
        imported_names=imported_names,
        is_relative=is_relative,
        resolved_file=None,
        bindings=bindings,
        is_reexport=is_reexport,
        is_module_declaration=is_module_declaration,
    )


def generic_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    return [_generic_import(stmt_node, module_text, raw, language, src)]


GENERIC_IMPORTS = ImportExtractor(statement=generic_statement)
