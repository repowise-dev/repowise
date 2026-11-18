"""TypeScript / JavaScript helpers: object-literal methods and functions assigned to a property."""

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

# Bindings that own an object literal, with the field holding their name: a
# property key, a declarator, a TS class field, a JS class field.
_TS_OBJECT_OWNER_FIELDS = {
    "pair": "key",
    "variable_declarator": "name",
    "public_field_definition": "name",
    "field_definition": "property",
}

_TS_OWNER_NAME_TYPES = frozenset(
    {"identifier", "property_identifier", "private_property_identifier"}
)

_CALLABLE_KINDS = frozenset({"function", "method"})


def _ts_is_named_function(node: Node) -> bool:
    if node.type in _TS_NAMED_FUNCTIONS:
        return True
    return (
        node.type in _TS_FUNCTION_VALUES
        and node.parent is not None
        and node.parent.type in _TS_VALUE_BINDERS
    )


def _ts_nested_object_method_owner(
    def_node: Node, src: str, symbol_kinds: dict[str, str]
) -> str | None:
    """The owner of an object-literal method the callable filter would drop.

    ``const Foo = make("Foo", (inst) => install(inst, { gt() {...} }))``: the
    anonymous callback names nothing, so ``gt`` is the nearest name its calls
    can be attributed to, as a type checker does, and ``Foo`` qualifies its id
    so a same-named method of another object keeps a distinct one.

    None when a named function encloses the method (it stays a local of that
    function), when no callable encloses it (the filter keeps it already, under
    its existing id), or when the nearest owner has no plain name (a string
    key, a destructuring pattern), since an unqualified id could collide.
    """
    if def_node.type != "method_definition":
        return None
    parent = def_node.parent
    if parent is None or parent.type != "object":
        return None
    owner: str | None = None
    owner_seen = False
    under_callable = False
    ancestor = parent.parent
    while ancestor is not None:
        if _ts_is_named_function(ancestor):
            return None
        if not owner_seen and ancestor.type in _TS_OBJECT_OWNER_FIELDS:
            owner_seen = True
            name = ancestor.child_by_field_name(_TS_OBJECT_OWNER_FIELDS[ancestor.type])
            if name is not None and name.type in _TS_OWNER_NAME_TYPES:
                owner = node_text(name, src)
        if symbol_kinds.get(ancestor.type) in _CALLABLE_KINDS:
            under_callable = True
        ancestor = ancestor.parent
    return owner if under_callable else None


# Scopes whose body runs when called. An assignment in one binds per call, not
# once per module, so it declares nothing a caller elsewhere can reach.
_TS_FUNCTION_SCOPES = _TS_NAMED_FUNCTIONS | _TS_FUNCTION_VALUES


def _ts_is_iife(scope: Node) -> bool:
    """*scope* is called where it is written: ``(function () {...})()``,
    ``(function () {...}).call(this)``, ``!function () {...}()``. Its body runs at
    load, so the module pattern's wrapper hides nothing."""
    node = scope
    while node.parent is not None and node.parent.type == "parenthesized_expression":
        node = node.parent
    parent = node.parent
    if parent is not None and parent.type == "member_expression":
        if parent.child_by_field_name("object") != node:
            return False
        node, parent = parent, parent.parent
    return (
        parent is not None
        and parent.type == "call_expression"
        and parent.child_by_field_name("function") == node
    )


def ts_assignment_runs_at_load(assignment: Node) -> bool:
    """True when ``obj.f = function () {}`` runs once, as the module loads.

    False inside a function body (a constructor's per-instance members, a
    callback's handlers), unless that function is an IIFE wrapping the module.
    """
    ancestor = assignment.parent
    while ancestor is not None:
        if ancestor.type in _TS_FUNCTION_SCOPES and not _ts_is_iife(ancestor):
            return False
        ancestor = ancestor.parent
    return True


def ts_assignment_owner(assignment: Node, src: str) -> str | None:
    """``Foo`` in ``Foo.prototype.m = ...`` or ``Foo.m = ...``, else None.

    A candidate only: it owns the function when the file declares it at the
    top level. ``exports.m``, ``a.b.m`` and the like have none.
    """
    left = assignment.child_by_field_name("left")
    target = left.child_by_field_name("object") if left is not None else None
    if target is not None and target.type == "member_expression":
        prop = target.child_by_field_name("property")
        if prop is None or node_text(prop, src) != "prototype":
            return None
        target = target.child_by_field_name("object")
    if target is None or target.type != "identifier":
        return None
    return node_text(target, src)
