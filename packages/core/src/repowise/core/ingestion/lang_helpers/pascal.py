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

    ``function TCalculator.Add(...): Integer;`` in an implementation
    section is captured by pascal.scm's ``genericDot`` patterns, where the
    bare ``@symbol.name`` is the ``rhs`` (``Add``) and the qualifying type
    lives in the sibling ``lhs`` field (``TCalculator``). Nesting-based
    ``_find_parent`` can't see this: the ``defProc`` node sits in the
    unit's implementation section, physically outside the class's
    ``declType`` body declared in the interface section. Handles both the
    plain (``genericDot rhs: identifier``) and generic-method
    (``genericDot rhs: genericTpl entity: identifier``) query shapes.

    Uses ``node_text`` (tree-sitter's own byte-accurate decode), not raw
    ``src`` byte-offset slicing — Pascal identifiers and unit names are
    frequently non-ASCII (Cyrillic) in this codebase's real-world sources,
    and slicing a decoded ``str`` by *byte* offsets misaligns on any
    multi-byte character.

    Returns ``None`` when the name node isn't inside a qualified header
    (i.e. a free function/procedure).
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

    Pascal declares a method's signature once in the ``interface`` section
    (``declProc``, no body) and its full body once in the
    ``implementation`` section (``defProc``) — two distinct physical AST
    nodes pascal.scm both legitimately captures (see the query file's
    comment). Once ``_find_parent`` (nesting) and ``_qualified_pascal_parent``
    (the ``TFoo.Method`` header) resolve both to the same
    ``(parent_name, name)``, keep only the ``defProc`` version: it carries
    the real body, which is what ``get_symbol`` should return, and the
    ``declProc`` duplicate would otherwise leave two ``Add`` nodes in the
    graph for one logical method.

    Keyed on ``(parent_name, signature)`` — normalized, see
    ``_pascal_dedupe_key`` — rather than just ``(parent_name, name)`` so
    that Pascal ``overload;`` siblings (same name, different parameter
    lists) are told apart: an interface-only overload must survive even
    when a *different* overload of the same name has a same-file
    implementation (verified against a reproduction where a 2-overload
    class with only one variant implemented was silently losing the
    other variant's interface declaration).

    Normalization matters in practice, not just in theory: scanned
    against a real ~150-file Delphi codebase, 168 method pairs shared a
    class+name but escaped a raw-signature-text match — almost all of
    them a long parameter list wrapped across lines differently between
    the compact interface declaration and the implementation (extremely
    common Delphi formatting), a handful differing only by identifier
    case (Pascal is case-insensitive, so ``TFoo.Add`` and
    ``TFOO.ADD`` name the same method). ``_pascal_dedupe_key`` strips all
    whitespace and lowercases before comparing so both collapse
    correctly.

    Still imperfect: Pascal's compiler doesn't require parameter *names*
    to match between an interface declaration and its implementation
    (only the types, for overload resolution), so a same-file rename
    between the two still produces different normalized keys and defeats
    this dedup, leaving both symbols. Unlike the whitespace/case cases
    above, no evidence of this actually happening was found in the real
    codebase this was checked against — left as a documented gap rather
    than parsing parameter types out of ``declArgs`` for an exact match.
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

    Whitespace-insensitive (multi-line parameter lists get reformatted
    between the interface declaration and the implementation constantly
    in real Delphi code) and case-insensitive (identifiers are
    case-insensitive in Pascal, so this needs to hold for the *class*
    name half of the key too, not just the signature).
    """
    parent_key = parent_name.lower() if parent_name else None
    sig_key = _WHITESPACE_RE.sub("", signature).lower()
    return (parent_key, sig_key)
