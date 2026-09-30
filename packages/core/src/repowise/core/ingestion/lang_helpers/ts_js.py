"""TypeScript / JavaScript object-literal method helpers."""

from __future__ import annotations

from tree_sitter import Node

from ..extractors import node_text

# Functions that carry their own name.
_TS_NAMED_FUNCTIONS = frozenset(
    {"function_declaration", "generator_function_declaration", "method_definition"}
)

# Function values, named only by what they are bound to.
_TS_FUNCTION_VALUES = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function"}
)

# What names a function value: ``const f = () => {}``, ``{ f: () => {} }``, a
# class field, ``export default () => {}``.
_TS_VALUE_BINDERS = frozenset(
    {
        "variable_declarator",
        "pair",
        "public_field_definition",
        "field_definition",
        "export_statement",
    }
)

# Bindings that qualify an object-literal method's id: a declarator, a TS
# class field (``name``) or a JS class field (``property``).
_TS_OBJECT_OWNERS = frozenset(
    {"variable_declarator", "public_field_definition", "field_definition"}
)

_TS_OWNER_NAME_TYPES = frozenset(
    {"identifier", "property_identifier", "private_property_identifier"}
)


def _ts_is_named_function(node: Node) -> bool:
    if node.type in _TS_NAMED_FUNCTIONS:
        return True
    return (
        node.type in _TS_FUNCTION_VALUES
        and node.parent is not None
        and node.parent.type in _TS_VALUE_BINDERS
    )


def _ts_object_method_is_top_named(def_node: Node) -> bool:
    """True for an object-literal method no named function encloses.

    ``install(inst, { gt() {...} })`` inside an anonymous callback: ``gt`` is
    the nearest name its calls can be attributed to, as a type checker does.
    A method inside a named function stays a local of that function.
    """
    if def_node.type != "method_definition":
        return False
    parent = def_node.parent
    if parent is None or parent.type != "object":
        return False
    ancestor = parent.parent
    while ancestor is not None:
        if _ts_is_named_function(ancestor):
            return False
        ancestor = ancestor.parent
    return True


def _ts_object_method_owner(def_node: Node, src: str) -> str | None:
    """The binding that qualifies an object-literal method's id.

    ``Foo`` in ``const Foo = make({ gt() {} })``, so same-named methods of two
    objects in one file keep distinct ids. None when nothing binds the object.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in _TS_OBJECT_OWNERS:
            name = ancestor.child_by_field_name("name") or ancestor.child_by_field_name("property")
            if name is not None and name.type in _TS_OWNER_NAME_TYPES:
                return node_text(name, src)
            return None
        ancestor = ancestor.parent
    return None
