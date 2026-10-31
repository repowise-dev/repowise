"""Pascal qualified-parent and interface/implementation dedupe helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

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


Visibility = Literal["public", "private", "protected", "internal"]

_SECTION_VISIBILITY: dict[str, Visibility] = {
    "kPrivate": "private",
    "kProtected": "protected",
    "kPublic": "public",
    "kPublished": "public",
}


def _pascal_visibility(def_node: Node) -> Visibility:
    """Who can name the declaration at *def_node*.

    A class member takes its ``declSection`` keyword (``strict private`` is
    private); one before any section keyword is public. Outside a class, a
    declaration in the unit's ``implementation`` section is private to the
    unit: only one in ``interface`` is exported. A routine defined in
    ``implementation`` takes the visibility of its ``interface`` declaration
    (:func:`_inherit_pascal_declared_visibility`).
    """
    node = def_node.parent
    while node is not None:
        if node.type == "declSection":
            keyword = next((c.type for c in node.children if c.type in _SECTION_VISIBILITY), None)
            return _SECTION_VISIBILITY.get(keyword or "", "public")
        if node.type in ("declClass", "declIntf", "declHelper", "interface"):
            return "public"
        if node.type == "implementation":
            return "private"
        node = node.parent
    return "public"


def _key(symbol: Symbol) -> tuple[str | None, str]:
    return _pascal_dedupe_key(symbol.parent_name, symbol.signature)


def _orphaned_declarations(
    symbols: list[Symbol], node_types: list[str]
) -> dict[int, tuple[Symbol, Symbol]]:
    """``{id(declaration): (declaration, method body)}`` for declarations that lost their class.

    An error-recovered class body leaves later member declarations
    parentless. One pairs with the method body of its signature only when no
    free routine has that signature and exactly one method body does: with two
    candidates the class cannot be told, and the declaration stays.
    """
    pairs = list(zip(symbols, node_types, strict=True))
    impl_keys = {_key(s) for s, nt in pairs if nt == "defProc"}
    method_bodies: dict[str, list[Symbol]] = {}
    for s, nt in pairs:
        if nt == "defProc" and s.parent_name:
            method_bodies.setdefault(_key(s)[1], []).append(s)
    orphans: dict[int, tuple[Symbol, Symbol]] = {}
    for s, nt in pairs:
        if nt != "declProc" or s.parent_name is not None or _key(s) in impl_keys:
            continue
        bodies = method_bodies.get(_key(s)[1], [])
        if len(bodies) == 1:
            orphans[id(s)] = (s, bodies[0])
    return orphans


def _inherit_pascal_declared_visibility(symbols: list[Symbol], node_types: list[str]) -> None:
    """Give each body in ``implementation`` its declaration's visibility, in place.

    A method body whose declaration was not found is public; a free routine
    keeps the unit-private visibility its section gave it.
    """
    pairs = list(zip(symbols, node_types, strict=True))
    declared = {_key(s): s.visibility for s, nt in pairs if nt == "declProc"}
    for declaration, body in _orphaned_declarations(symbols, node_types).values():
        declared[_key(body)] = declaration.visibility
    for s, nt in pairs:
        if nt == "defProc":
            s.visibility = declared.get(_key(s), "public" if s.parent_name else s.visibility)


def _dedupe_pascal_interface_symbols(
    symbols: list[Symbol], node_types: list[str]
) -> list[Symbol]:
    """Drop an interface-section method signature once its implementation
    is also present, so the two don't become two graph nodes for one method.

    Pascal declares a signature in the ``interface`` section (``declProc``,
    no body) and the body in ``implementation`` (``defProc``). Once both
    resolve to one parent, only the ``defProc`` is kept, since it carries the
    body ``get_symbol`` should return. A declaration that lost its class
    (:func:`_orphaned_declarations`) is dropped for the body it pairs with.

    Keyed on the normalized signature (see ``_pascal_dedupe_key``) rather than
    the name, so an interface-only ``overload;`` sibling survives when a
    different overload of the same name is implemented. Parameter names need
    not match between the two halves in Pascal, so a renamed parameter still
    defeats the dedupe and leaves both symbols.
    """
    impl_keys = {_key(s) for s, nt in zip(symbols, node_types, strict=True) if nt == "defProc"}
    orphans = _orphaned_declarations(symbols, node_types)
    return [
        s
        for s, nt in zip(symbols, node_types, strict=True)
        if not (nt == "declProc" and (_key(s) in impl_keys or id(s) in orphans))
    ]


def _pascal_dedupe_key(parent_name: str | None, signature: str) -> tuple[str | None, str]:
    """Normalize a (parent, signature) pair for Pascal's interface/impl dedup.

    Whitespace-insensitive, because the two halves wrap long parameter lists
    differently, and case-insensitive, because Pascal identifiers are.
    """
    parent_key = parent_name.lower() if parent_name else None
    sig_key = _WHITESPACE_RE.sub("", signature).lower()
    return (parent_key, sig_key)
