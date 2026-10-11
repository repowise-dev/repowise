"""Shared helpers for Python ``@overload`` stubs."""

from __future__ import annotations

from tree_sitter import Node

from .extractors.helpers import node_text


def is_python_overload(def_node: Node, src: str) -> bool:
    """True when *def_node* is a type-checker-only ``@overload`` signature stub."""
    parent = def_node.parent
    if parent is None or parent.type != "decorated_definition":
        return False
    for decorator in parent.children:
        if decorator.type != "decorator":
            continue
        # The expression, not the node text: a trailing comment sits inside it.
        expr = next((c for c in decorator.named_children if c.type != "comment"), None)
        # ``overload``, ``typing.overload``, or through an alias (``t.overload``).
        if expr is not None and node_text(expr, src).rsplit(".", 1)[-1] == "overload":
            return True
    return False
