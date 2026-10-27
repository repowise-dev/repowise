"""Pascal qualified-parent and interface/implementation dedupe helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from tree_sitter import Node

from ..extractors import node_text

if TYPE_CHECKING:
    from ..models import Symbol

_WHITESPACE_RE = re.compile(r"\s+")


def _qualified_pascal_parent(name_node: Node, src: str) -> str | None:
    """Return the owning class for a Pascal out-of-line method header.

    In ``function TCalculator.Add(...)`` the captured name is the
    ``genericDot``'s ``rhs`` and the class is its ``lhs``. Nesting cannot find
    it: the ``defProc`` sits in the implementation section, outside the
    class's ``declType``. Handles the plain and the generic-method
    (``rhs: genericTpl``) shapes. Reads with ``node_text`` because slicing a
    decoded ``str`` by byte offsets misaligns on non-ASCII identifiers.

    Returns ``None`` for a free function/procedure.
    """
    parent = name_node.parent
    if parent is not None and parent.type == "genericTpl":
        parent = parent.parent
    if parent is None or parent.type != "genericDot":
        return None
    lhs = parent.child_by_field_name("lhs")
    if lhs is None:
        return None
    text = node_text(lhs, src).strip()
    return text or None


def _dedupe_pascal_interface_symbols(
    symbols: list[Symbol], node_types: list[str]
) -> list[Symbol]:
    """Drop an interface-section method signature once its implementation
    is also present, so the two don't become two graph nodes for one method.

    Pascal declares a signature in the ``interface`` section (``declProc``,
    no body) and the body in ``implementation`` (``defProc``). Once both
    resolve to one parent, only the ``defProc`` is kept, since it carries the
    body ``get_symbol`` should return.

    Keyed on the normalized signature (see ``_pascal_dedupe_key``) rather than
    the name, so an interface-only ``overload;`` sibling survives when a
    different overload of the same name is implemented. Parameter names need
    not match between the two halves in Pascal, so a renamed parameter still
    defeats the dedupe and leaves both symbols.
    """
    impl_keys = {
        _pascal_dedupe_key(s.parent_name, s.signature)
        for s, nt in zip(symbols, node_types, strict=True)
        if nt == "defProc"
    }
    return [
        s
        for s, nt in zip(symbols, node_types, strict=True)
        if not (nt == "declProc" and _pascal_dedupe_key(s.parent_name, s.signature) in impl_keys)
    ]


def _pascal_dedupe_key(parent_name: str | None, signature: str) -> tuple[str | None, str]:
    """Normalize a (parent, signature) pair for Pascal's interface/impl dedup.

    Whitespace-insensitive, because the two halves wrap long parameter lists
    differently, and case-insensitive, because Pascal identifiers are.
    """
    parent_key = parent_name.lower() if parent_name else None
    sig_key = _WHITESPACE_RE.sub("", signature).lower()
    return (parent_key, sig_key)
