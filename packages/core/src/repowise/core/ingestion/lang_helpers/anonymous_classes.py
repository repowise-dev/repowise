"""Id segments for members of anonymous classes.

Java ``new Foo() { void m() {} }`` and Kotlin ``object : Foo { fun m() {} }``
declare a class with no name. Its members are named by the declarations that
hold it and its supertype, ``Gson::FILTER::ReflectionAccessFilter::check`` for
a field initializer and ``Gson::make::Comparator::compare`` inside a method, so
an edit elsewhere in the file never renames them. Two same-typed anonymous
classes held by one declaration are told apart by a source-order ordinal on the
second and later ones, ``Comparator$2``, javac's own convention.
"""

from __future__ import annotations

from tree_sitter import Node

from ..extractors import node_text
from ..language_configs import LANGUAGE_CONFIGS
from ..models import Symbol
from ..type_names import bare_type_name

# Declarations that name an anonymous class they hold: a field, local or
# property, a method or function, a constructor, an enum constant.
_HOLDER_TYPES = frozenset(
    {
        "variable_declarator",
        "method_declaration",
        "constructor_declaration",
        "property_declaration",
        "function_declaration",
        "enum_constant",
    }
)

# Where an ordinal is counted: the nearest holder, else the enclosing body.
_ORDINAL_SCOPES = _HOLDER_TYPES | {"class_body"}


def in_anonymous_class(def_node: Node, anonymous_types: frozenset[str]) -> bool:
    """Is *def_node* a member of an anonymous class body?"""
    body = def_node.parent
    return (
        body is not None
        and body.type == "class_body"
        and body.parent is not None
        and body.parent.type in anonymous_types
    )


def is_anonymous_member(symbol: Symbol) -> bool:
    """Is *symbol* named under an anonymous class? No bare name reaches one.

    In a language with anonymous classes only such a member has a parent of
    several segments (``Gson::FILTER::Filter``).
    """
    config = LANGUAGE_CONFIGS.get(symbol.language)
    return bool(
        config and config.anonymous_class_types and "::" in (symbol.parent_name or "")
    )


def scope_segment(
    node: Node,
    child: Node,
    in_anonymous: bool,
    anonymous_types: frozenset[str],
    src: str,
) -> str | None:
    """The id segment *node* adds for a member below it, reached through *child*.

    An anonymous class names its supertype and, once inside one, each holder
    its own name.
    """
    if node.type in anonymous_types and child.type == "class_body":
        return _anonymous_class_segment(node, src)
    if in_anonymous and node.type in _HOLDER_TYPES:
        return _holder_name(node, src)
    return None


def _holder_name(node: Node, src: str) -> str | None:
    name = node.child_by_field_name("name")
    if name is None and node.type == "property_declaration":
        # Kotlin ``val x = object : ..``: the name sits in a variable_declaration.
        declaration = next((c for c in node.children if c.type == "variable_declaration"), None)
        name = declaration.named_children[0] if declaration and declaration.named_children else None
    return node_text(name, src) or None


def _supertype_name(node: Node, src: str) -> str:
    """``new Map.Entry<K, V>() {..}`` -> ``Entry``; Kotlin's first supertype, or ``object``."""
    type_node = node.child_by_field_name("type")
    if type_node is None:
        specifiers = next((c for c in node.children if c.type == "delegation_specifiers"), None)
        if specifiers is not None and specifiers.named_children:
            first = specifiers.named_children[0]
            type_node = next((n for n in _descendants(first) if n.type == "user_type"), first)
    return bare_type_name(node_text(type_node, src)) or "object"


def _anonymous_class_segment(node: Node, src: str) -> str:
    name = _supertype_name(node, src)
    scope = _ordinal_scope(node)
    ordinal = 1
    for other in _descendants(scope):
        if other.start_byte >= node.start_byte:
            break
        if (
            other.type == node.type
            and any(c.type == "class_body" for c in other.children)
            and _ordinal_scope(other) == scope
            and _supertype_name(other, src) == name
        ):
            ordinal += 1
    return name if ordinal == 1 else f"{name}${ordinal}"


def _ordinal_scope(node: Node) -> Node:
    ancestor = node.parent
    while ancestor is not None and ancestor.parent is not None:
        if ancestor.type in _ORDINAL_SCOPES:
            return ancestor
        ancestor = ancestor.parent
    return ancestor if ancestor is not None else node


def _descendants(node: Node):
    """*node* and its descendants in source order."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))
