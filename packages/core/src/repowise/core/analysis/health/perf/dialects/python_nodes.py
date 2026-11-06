"""Tree-sitter helpers for Python syntax the perf dialect reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .base import BasePerfDialect
from .python_lexicon import (
    _PY_BINDING_PARENTS,
    _PY_BINDING_WRAPPERS,
    _PY_CONSTANT_RHS,
    _PY_DEF_KINDS,
    _PY_SCHEMA_SQL_RE,
    _PY_STRING_KINDS,
)

if TYPE_CHECKING:
    from tree_sitter import Node


def _first_arg(call: Node) -> Node | None:
    args = call.child_by_field_name("arguments")
    return next((c for c in args.children if c.is_named), None) if args is not None else None


def _runs_schema_statement(call: Node, value_of: Callable[[Node, bytes], Node | None]) -> bool:
    """*call*'s statement is DDL or a PRAGMA (``_PY_SCHEMA_SQL_RE``): its first
    argument, the value *value_of* says that name holds, or the text in ``text(...)``.

    Ceiling: Python only, and a module-level statement constant is not followed.
    """
    statement = _first_arg(call)
    if statement is not None and statement.type == "identifier":
        statement = value_of(call, statement.text or b"")
    statement = _text_arg(statement) if statement is not None and statement.type == "call" else statement
    return statement is not None and statement.type in _PY_STRING_KINDS and bool(
        _PY_SCHEMA_SQL_RE.match(_text(statement))
    )


def _text_arg(call: Node) -> Node | None:
    """The statement in ``text("...")``; any other call is no literal statement."""
    return _first_arg(call) if _field_text(call, "function").rsplit(".", 1)[-1] == "text" else None


def _binds(ident: Node) -> bool:
    """Whether *ident* is a name being bound (assigned, imported, a parameter)."""
    cur, parent = ident, ident.parent
    while parent is not None and parent.type in _PY_BINDING_WRAPPERS:
        cur, parent = parent, parent.parent
    if parent is None:
        return False
    if parent.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
        return parent.child_by_field_name("left") == cur
    if parent.type in ("function_definition", "class_definition"):
        return parent.child_by_field_name("name") == cur
    return parent.type in _PY_BINDING_PARENTS


def _bindings(scope: Node, name: bytes) -> list[Node]:
    """Identifiers binding *name* in *scope* itself, not in a nested scope."""
    found: list[Node] = []
    stack = list(scope.children)
    while stack:
        node = stack.pop()
        if node.type in _PY_DEF_KINDS:
            stack.extend(c for c in (node.child_by_field_name("name"),) if c is not None)
            continue
        if node.type == "identifier" and node.text == name and _binds(node):
            found.append(node)
        stack.extend(node.children)
    return found


_SPLITS = frozenset({"split", "splitlines"})


def _module_int_constant(name: Node) -> bool:
    return _module_constant(name, lambda value: value.type == "integer")


def _splits_constant(call: Node) -> bool:
    """``"a b".split()`` or ``SCHEMA.split(";")`` with ``SCHEMA`` a module string
    literal: the source fixes every piece."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "attribute" or _field_text(fn, "attribute") not in _SPLITS:
        return False
    text = fn.child_by_field_name("object")
    return text is not None and (_plain_string(text) or _module_constant(text, _plain_string))


def _plain_string(node: Node) -> bool:
    """A string literal with no ``{...}`` interpolation, so its text is fixed."""
    return node.type in _PY_STRING_KINDS and not any(
        n.type == "interpolation" for n in BasePerfDialect._walk(node)
    )


def _module_constant(name: Node, is_literal: Callable[[Node], bool]) -> bool:
    """An ALL_CAPS name its own module binds exactly once, to a value *is_literal* accepts
    (``MAX = 5``), and no enclosing function rebinds or shadows.

    The spelling alone proves nothing (``NUM_USERS`` grows), so a name rebound,
    imported, augmented, written through ``global``, shadowed by a parameter or
    a local, or bound to anything but a literal stays unknown.
    """
    text = name.text or b""
    if name.type != "identifier" or len(text) < 2 or not text.decode().isupper():
        return False
    module = _unshadowed_module(name, text)
    if module is None:
        return False
    bound = _bindings(module, text)
    if len(bound) != 1 or bound[0].parent is None or bound[0].parent.type != "assignment":
        return False
    right = bound[0].parent.child_by_field_name("right")
    return right is not None and is_literal(right)


def _unshadowed_module(name: Node, text: bytes) -> Node | None:
    """The module root, when no enclosing function binds *text* and no ``global`` names it."""
    scope = name.parent
    while scope is not None and scope.parent is not None:
        if scope.type in ("function_definition", "lambda") and _bindings(scope, text):
            return None
        scope = scope.parent
    if scope is None or any(
        n.type == "global_statement" and any(c.text == text for c in n.named_children)
        for n in BasePerfDialect._walk(scope)
    ):
        return None
    return scope


def _int_literal(node: Node) -> bool:
    return node.type == "integer" or (
        node.type == "unary_operator" and any(c.type == "integer" for c in node.children)
    )


def _literal_range(call: Node) -> bool:
    """``range(...)`` whose every argument is an integer literal; ``range(len(x))`` is data-dependent."""
    fn = call.child_by_field_name("function")
    if fn is None or (fn.text or b"").decode("utf-8", "replace") != "range":
        return False
    args = call.child_by_field_name("arguments")
    if args is None:
        return False
    return all(_int_literal(a) for a in args.children if a.is_named)


def _is_or(expr: Node) -> bool:
    return expr.type == "boolean_operator" and any(c.type == "or" for c in expr.children)


def _slice_bounds(subscript: Node) -> tuple[Node | None, Node] | None:
    """``(start, stop)`` of a one-colon slice that has a stop."""
    piece = subscript.child_by_field_name("subscript")
    if piece is None or piece.type != "slice" or sum(c.type == ":" for c in piece.children) != 1:
        return None
    colon = next(i for i, c in enumerate(piece.children) if c.type == ":")
    start = next((c for c in piece.children[:colon] if c.is_named), None)
    stop = next((c for c in piece.children[colon + 1 :] if c.is_named), None)
    return None if stop is None else (start, stop)


def _string_content(string: Node) -> str | None:
    return next((c.text.decode() for c in string.children if c.type == "string_content"), None)


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _field_text(node: Node, field: str) -> str:
    child = node.child_by_field_name(field)
    return _text(child) if child is not None else ""


def _value_refs(expr: Node) -> list[Node]:
    """The names and dotted paths *expr* reads values from: not a callee
    (``tuple`` in ``tuple(xs)``), a keyword, or an ALL_CAPS constant."""
    refs = []
    stack = [expr]
    while stack:
        node = stack.pop()
        if node.type not in ("identifier", "attribute"):
            stack.extend(node.children)
            continue
        parent = node.parent
        callee = parent is not None and parent.child_by_field_name("function") == node
        keyword = parent is not None and parent.type == "keyword_argument" and (
            parent.child_by_field_name("name") == node
        )
        last = node.child_by_field_name("attribute") if node.type == "attribute" else node
        constant = last is not None and _text(last).strip("_").isupper()
        if callee and node.type == "attribute":
            # ``self.ids.copy()`` still reads ``self.ids``.
            stack.extend(filter(None, [node.child_by_field_name("object")]))
        elif not (callee or keyword or constant):
            refs.append(node)
    return refs


def _string_value(node: Node) -> str | None:
    return _text(node).strip("\"'") if node.type == "string" else None


def _in_filter(call: Node, args: list[Node]) -> tuple[str, Node] | None:
    """``(column, keys)`` of ``Model.col.in_(keys)`` or PostgREST ``.in_("col", keys)``."""
    if len(args) == 2:
        column = _string_value(args[0])
        return (column, args[1]) if column else None
    fn = call.child_by_field_name("function")
    receiver = fn.child_by_field_name("object") if fn is not None else None
    if len(args) != 1 or receiver is None or receiver.type != "attribute":
        return None
    column = _field_text(receiver, "attribute")
    return (column, args[0]) if column else None


def _fixed_set(value: Node | None) -> bool:
    """A non-empty literal of scalars; an empty one is a container filled later."""
    return (
        value is not None
        and value.type in _PY_CONSTANT_RHS
        and bool(value.named_children)
        and all(_scalar_literal(item) for item in value.named_children)
    )


_PY_SCALARS = frozenset({"string", "concatenated_string", "integer", "float", "true", "false", "none"})


def _scalar_literal(item: Node) -> bool:
    """``"a"`` / ``3`` / ``("a", 1)`` / ``"k": v`` by its key: an entry the source
    spells out. A name or call in the literal (``[ROOT / "a", repo.path]``) may
    stand for anything, so the table is not shown to be a fixed set."""
    if item.type == "pair":
        key = item.child_by_field_name("key")
        return key is not None and key.type in _PY_SCALARS
    if item.type == "tuple":
        return all(child.type in _PY_SCALARS for child in item.named_children)
    return item.type in _PY_SCALARS
