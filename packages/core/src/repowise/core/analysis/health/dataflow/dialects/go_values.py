"""Which Go variable a write through a field or an index changes.

Go copies a struct and an array on assignment and on a call. ``p.x = 1`` with
``p`` a struct therefore changes the variable ``p`` itself, and a function
handed ``p`` changes only its own copy. A pointer, a map and a slice share
their storage with the copy, so the same write through one of those changes
nothing the variable owns.

Everything here is read from one file's tree: the declared type of the name in
its function, the types this file declares, and the result types of the
functions it declares. What the file does not say is ``UNKNOWN``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

_SELECTOR = "selector_expression"
_INDEX = "index_expression"
_SHORT_VAR = "short_var_declaration"
_RANGE_CLAUSE = "range_clause"
# Each of these is a reference to storage a copy of the variable still shares.
_REFERENCE_TYPES = frozenset(
    {"pointer_type", "map_type", "slice_type", "channel_type", "function_type", "interface_type"}
)
_NAMED_TYPES = frozenset({"type_identifier", "generic_type"})
_FUNCTION_KINDS = frozenset({"function_declaration", "method_declaration", "func_literal"})
_ALLOCATORS = frozenset({b"new", b"make"})


def written_value(target: Node) -> Node | None:
    """The variable a field or index write changes, when it holds the value.

    ``p.x = 1`` with ``p`` a struct, ``a[0] = 1`` with ``a`` an array, and any
    chain of the two (``p.q.x``, ``a[0].x``) write the variable itself: Go
    copies both on assignment and on a call, so a helper handed ``p`` changes
    its own copy. Returns the base identifier then, and also where the file
    does not say what the path crosses (``_UNKNOWN_IS_VALUE``); None when it
    crosses a pointer, a slice or a map, and for a name that is no local of
    the function. See :func:`storage`.
    """
    base, kind = storage(target)
    if kind == VALUE or (kind == UNKNOWN and _UNKNOWN_IS_VALUE):
        return base
    return None


VALUE, REFERENCE, UNKNOWN = "value", "reference", "unknown"
#: A write whose target type this file does not settle counts as a write of
#: the variable. A helper that returns the variable is right whether it holds
#: a value or a reference; one that does not is wrong for a value.
_UNKNOWN_IS_VALUE = True
_REF = "ref"  # the type of ``&x`` / ``new(T)`` / ``make(...)``: some reference


def storage(target: Node) -> tuple[Node | None, str]:
    """``(base, kind)`` for a selector / index write target: the variable at
    the root of the path, and whether writing through the path writes it."""
    path: list[Node] = []
    node = target
    while node.type in (_SELECTOR, _INDEX, "parenthesized_expression"):
        if node.type != "parenthesized_expression":
            path.append(node)
        inner = node.child_by_field_name("operand") or (
            node.named_children[0] if node.named_children else None
        )
        if inner is None:
            return None, REFERENCE
        node = inner
    if node.type != "identifier":
        return None, REFERENCE  # a call result, ``*p``: nothing a helper is handed
    typ = _declared_type(node, 0)
    if typ is _NOT_LOCAL:
        return None, REFERENCE  # a package variable or a captured one: shared storage
    for hop in reversed(path):
        typ = _step(typ, hop)
        if typ is None:
            return node, UNKNOWN
        if typ == _REF:
            return node, REFERENCE
    return node, VALUE


def _step(typ: Node | str | None, hop: Node) -> Node | str | None:
    """The type *hop* (``.f`` or ``[i]``) reaches from a value of *typ*:
    ``_REF`` when the hop goes through a reference, None when unknown."""
    typ = _resolve(typ, hop)
    if typ is None or typ == _REF:
        return typ
    if typ.type in _REFERENCE_TYPES:
        return _REF
    if hop.type == _INDEX:
        return typ.child_by_field_name("element")  # only an array is left to have one
    field = hop.child_by_field_name("field")
    return _field_type(typ, field.text if field is not None else None)


def _resolve(typ: Node | str | None, at: Node) -> Node | str | None:
    """*typ* with a name declared in this file replaced by what it names."""
    for _ in range(8):
        if typ is None or typ == _REF:
            return typ
        if typ.type == "parenthesized_type":
            typ = typ.named_children[0] if typ.named_children else None
        elif typ.type in _NAMED_TYPES:
            name = typ if typ.type == "type_identifier" else typ.child_by_field_name("type")
            typ = _type_spec(at, name.text if name is not None else None)
        else:
            return typ
    return None


def _root(node: Node) -> Node:
    while node.parent is not None:
        node = node.parent
    return node


def _type_spec(at: Node, name: bytes | None) -> Node | None:
    """The type a top-level ``type <name> ...`` in *at*'s file declares."""
    for decl in _root(at).named_children:
        if decl.type != "type_declaration":
            continue
        for spec in decl.named_children:
            spec_name = spec.child_by_field_name("name")
            if spec_name is not None and spec_name.text == name:
                return spec.child_by_field_name("type")
    return None


def _field_type(struct: Node, name: bytes | None) -> Node | None:
    for fields in struct.named_children:
        for decl in fields.named_children:
            if any(c.type == "field_identifier" and c.text == name for c in decl.named_children):
                return decl.child_by_field_name("type")
    return None


#: The name has no declaration in its function.
_NOT_LOCAL = "not-local"


def _declared_type(ident: Node, depth: int) -> Node | str | None:
    """The type *ident* was declared with, read from its function: a parameter,
    receiver or named result, ``var x T``, or ``x := <expr>`` where the
    expression's type can be read (:func:`_expr_type`). None when it cannot,
    and when two declarations of the name in the function disagree."""
    fn = ident.parent
    while fn is not None and fn.type not in _FUNCTION_KINDS:
        fn = fn.parent
    if fn is None:
        return _NOT_LOCAL
    name = ident.text
    found: list[Node | str | None] = []
    stack = [fn]
    while stack:
        node = stack.pop()
        t = node.type
        if t in ("parameter_declaration", "var_spec"):
            if any(c.type == "identifier" and c.text == name for c in node.named_children):
                typ = node.child_by_field_name("type")
                found.append(typ if typ is not None else _bound_type(node, name, depth))
        elif t == "variadic_parameter_declaration":
            # ``p ...T`` is a ``[]T`` inside the function; its ``type`` is ``T``.
            if any(c.type == "identifier" and c.text == name for c in node.named_children):
                found.append(_REF)
        elif t == _SHORT_VAR and _binds(node, name):
            found.append(_bound_type(node, name, depth))
        elif t == _RANGE_CLAUSE and _binds(node, name):
            found.append(None)
        if node is fn or t not in _FUNCTION_KINDS:
            stack.extend(node.named_children)
    if not found:
        return _NOT_LOCAL
    if any(typ is None for typ in found):
        return None
    kinds = {typ if isinstance(typ, str) else (typ.type, typ.text) for typ in found}
    return found[0] if len(kinds) == 1 else None


def _binds(decl: Node, name: bytes | None) -> bool:
    left = decl.child_by_field_name("left")
    return left is not None and any(
        c.type == "identifier" and c.text == name for c in left.named_children
    )


def _bound_type(decl: Node, name: bytes | None, depth: int) -> Node | str | None:
    """The type of the expression *name* is initialised with in *decl*."""
    left = decl.child_by_field_name("left")
    names = (
        list(left.named_children)
        if left is not None
        else [c for c in decl.named_children if c.type == "identifier"]
    )
    values = decl.child_by_field_name("right") or decl.child_by_field_name("value")
    if values is None:
        return None
    # Fewer values than names is one call with several results or a comma-ok
    # read. Only the first name is paired then: ``p, ok := v.(T)`` gives ``p``
    # the asserted type, and a call with several results has no single type.
    for target, value in zip(names, values.named_children, strict=False):
        if target.type == "identifier" and target.text == name:
            return _expr_type(value, depth)
    return None


def _expr_type(expr: Node, depth: int) -> Node | str | None:
    """The type of *expr* where the file says it: a composite literal, ``&x``,
    ``new`` / ``make``, a type assertion or conversion, another local, a
    field or element of one, a call of a function this file declares."""
    if depth > 4:
        return None
    t = expr.type
    if t == "parenthesized_expression":
        return _expr_type(expr.named_children[0], depth) if expr.named_children else None
    if t == "composite_literal":
        return expr.child_by_field_name("type")
    if t == "unary_expression":
        op = expr.child_by_field_name("operator")
        if op is not None and op.text == b"&":
            return _REF
        # ``p := *q`` copies what ``q`` points at. ``*`` is the only other
        # operator a pointer operand takes, so the operator needs no test.
        operand = expr.child_by_field_name("operand")
        pointer = _resolve(_expr_type(operand, depth + 1), expr) if operand is not None else None
        if pointer is not None and pointer != _REF and pointer.type == "pointer_type":
            return pointer.named_children[0] if pointer.named_children else None
        return None
    if t == "type_assertion_expression":
        return expr.child_by_field_name("type")
    if t == "identifier":
        typ = _declared_type(expr, depth + 1)
        return None if typ is _NOT_LOCAL else typ
    if t in (_SELECTOR, _INDEX):
        operand = expr.child_by_field_name("operand")
        inner = _expr_type(operand, depth + 1) if operand is not None else None
        if inner is None:
            return None
        if inner == _REF:
            return None  # through a pointer: the pointee's type is not at hand
        resolved = _resolve(inner, expr)
        if resolved is None or resolved == _REF:
            return None
        if t == _INDEX:
            return resolved.child_by_field_name("element")
        if resolved.type == "pointer_type":  # ``p.f`` reads through ``*T`` too
            resolved = _resolve(
                resolved.named_children[0] if resolved.named_children else None, expr
            )
        if resolved is None or resolved == _REF or resolved.type != "struct_type":
            return None
        field = expr.child_by_field_name("field")
        return _field_type(resolved, field.text if field is not None else None)
    if t == "call_expression":
        fn = expr.child_by_field_name("function")
        if fn is None or fn.type != "identifier":
            return None
        if fn.text in _ALLOCATORS:
            return _REF
        return _result_type(expr, fn.text)
    return None


def _result_type(at: Node, name: bytes | None) -> Node | None:
    """The single result type of the top-level function *name* in this file."""
    for decl in _root(at).named_children:
        if decl.type != "function_declaration":
            continue
        fn_name = decl.child_by_field_name("name")
        if fn_name is None or fn_name.text != name:
            continue
        result = decl.child_by_field_name("result")
        if result is None or result.type != "parameter_list":
            return result
        named = result.named_children  # ``(p P)``: one named result, or several
        if len(named) == 1 and len([c for c in named[0].named_children if c.type == "identifier"]) <= 1:
            return named[0].child_by_field_name("type")
        return None
    return None
