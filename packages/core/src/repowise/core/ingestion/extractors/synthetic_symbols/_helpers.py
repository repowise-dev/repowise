"""Shared helpers for synthetic-symbol providers.

Pure utilities used by ``csharp_mvvm``, ``lombok``, ``java_records``,
``kotlin_jvm``, and other per-source modules in this package. No
language-specific logic — anything that branches on attribute / source
generator belongs in the source-specific module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text

if TYPE_CHECKING:
    from tree_sitter import Node


def build_synthetic_symbol(
    *,
    name: str,
    kind: str,
    signature: str,
    start_line: int,
    end_line: int,
    file_info: FileInfo,
    parent_name: str | None,
    decorators: list[str] | None = None,
) -> Symbol:
    """Construct a ``Symbol`` instance for a generator-synthesised name.

    The synthesised symbol carries no docstring (the user never wrote it)
    and defaults to public visibility — generators emit public surfaces.
    Symbol IDs follow the same ``<path>::[<parent>::]<name>`` shape the
    real symbol pass uses, so the existing graph / dead-code passes
    treat them identically.
    """
    sym_id = (
        f"{file_info.path}::{parent_name}::{name}"
        if parent_name
        else f"{file_info.path}::{name}"
    )
    qualified = (
        f"{file_info.path}.{parent_name}.{name}"
        if parent_name
        else f"{file_info.path}.{name}"
    )
    return Symbol(
        id=sym_id,
        name=name,
        qualified_name=qualified,
        kind=kind,  # type: ignore[arg-type]
        signature=signature,
        start_line=start_line,
        end_line=end_line,
        docstring=None,
        decorators=decorators or [],
        visibility="public",
        is_async=False,
        language=file_info.language,
        parent_name=parent_name,
    )


def enclosing_type_name(node: "Node", src: str, ancestor_types: frozenset[str]) -> str | None:
    """Walk up to find the nearest type-defining ancestor and return its name."""
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in ancestor_types:
            name_node = ancestor.child_by_field_name("name")
            if name_node is not None:
                return node_text(name_node, src).strip()
        ancestor = ancestor.parent
    return None
