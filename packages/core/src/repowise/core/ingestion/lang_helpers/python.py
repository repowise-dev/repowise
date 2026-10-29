"""Lexical ownership of Python function symbols."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..models import Symbol


def nested_function_parent(node: Node) -> str | None:
    """The callable scope containing a named Python function, if it has one.

    Stop at the containing class so methods retain the parser's existing
    class-name convention. Classes local to a function are not indexed yet,
    so neither are their methods or closures.
    """
    if node.type != "function_definition":
        return None
    names: list[str] = []
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "class_definition":
            outer = ancestor.parent
            while outer is not None:
                if outer.type == "function_definition":
                    return None
                outer = outer.parent
            if not names:
                return None
            name = ancestor.child_by_field_name("name")
            if name is not None:
                names.append(name.text.decode("utf-8"))
            break
        if ancestor.type == "function_definition":
            name = ancestor.child_by_field_name("name")
            if name is not None:
                names.append(name.text.decode("utf-8"))
        ancestor = ancestor.parent
    return "::".join(reversed(names)) or None


def is_nested_function(symbol: Symbol) -> bool:
    """A Python function with a lexical owner, never a class member or export."""
    return symbol.language == "python" and symbol.kind == "function" and symbol.parent_name is not None


def definition_time_scopes(site: Node) -> set[tuple[int, int]]:
    """Nested definitions whose headers contain *site*, rather than their bodies.

    Defaults and annotations execute where the function is defined. AST body
    boundaries, not line comparisons, distinguish a one-line function's
    default argument from its body on the very same line.
    """
    scopes: set[tuple[int, int]] = set()
    child, ancestor = site, site.parent
    while ancestor is not None:
        if (
            ancestor.type == "function_definition"
            and child != ancestor.child_by_field_name("body")
            and nested_function_parent(ancestor) is not None
        ):
            scopes.add((ancestor.start_point[0] + 1, ancestor.end_point[0] + 1))
        child, ancestor = ancestor, ancestor.parent
    return scopes
