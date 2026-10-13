"""Loop bodies that keep one row per key: the in-code half of an over-fetch.

Three first-wins shapes, each keyed by something read off the loop row:
``d.setdefault(key, row)``, ``if key not in d: d[key] = row``, and
``if key in seen: continue`` followed by ``seen.add(key)``. A bare
``d[row.id] = row`` is a 1:1 projection, not a reduction, and never matches.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..complexity.ast_utils import _node_text

if TYPE_CHECKING:
    from tree_sitter import Node


def walk(node: Node, stop_at: frozenset[str] = frozenset()) -> Iterator[Node]:
    """Pre-order walk that does not enter a nested scope of a *stop_at* kind."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if current is node or current.type not in stop_at:
            stack.extend(current.children)


def ident(node: Node | None) -> str | None:
    """The name when *node* is a bare identifier, else ``None``."""
    return _node_text(node) if node is not None and node.type == "identifier" else None


def field_text(node: Node, field: str) -> str:
    child = node.child_by_field_name(field)
    return _node_text(child) if child is not None else ""


def references(node: Node, names: frozenset[str]) -> bool:
    return any(ident(n) in names for n in walk(node))


def _method_call(node: Node) -> tuple[str | None, str, list[Node]] | None:
    """``recv.method(args)`` as ``(recv name, method, named args)``."""
    if node.type == "expression_statement":
        node = next((c for c in node.children if c.is_named), node)
    function = node.child_by_field_name("function") if node.type == "call" else None
    if function is None or function.type != "attribute":
        return None
    args = node.child_by_field_name("arguments")
    named = [a for a in args.children if a.is_named] if args is not None else []
    return ident(function.child_by_field_name("object")), field_text(function, "attribute"), named


def _membership(stmt: Node) -> tuple[str, Node, str, Node] | None:
    """``if key [not] in name: ...`` as ``(operator, key, name, consequence)``."""
    if stmt.type != "if_statement":
        return None
    condition = stmt.child_by_field_name("condition")
    consequence = stmt.child_by_field_name("consequence")
    if condition is None or consequence is None or condition.type != "comparison_operator":
        return None
    operator = next((c.type for c in condition.children if c.type in ("in", "not in")), None)
    named = [c for c in condition.children if c.is_named]
    container = ident(named[-1]) if len(named) >= 2 else None
    if operator is None or container is None:
        return None
    return operator, named[0], container, consequence


def _row_names(for_stmt: Node) -> frozenset[str]:
    left = for_stmt.child_by_field_name("left")
    return frozenset(filter(None, (ident(n) for n in walk(left)))) if left else frozenset()


def _derived(stmts: list[Node], rows: frozenset[str]) -> frozenset[str]:
    """*rows* plus locals assigned from them (``rid = row["repo_id"]``), one hop."""
    derived = set(rows)
    for stmt in stmts:
        node = next((c for c in stmt.children if c.is_named), stmt)
        if node.type != "assignment":
            continue
        name = ident(node.child_by_field_name("left"))
        right = node.child_by_field_name("right")
        if name and right is not None and references(right, frozenset(derived)):
            derived.add(name)
    return frozenset(derived)


def _setdefault_key(stmt: Node, rows: frozenset[str], keys: frozenset[str]) -> Node | None:
    call = _method_call(stmt)
    if call is None or call[1] != "setdefault" or len(call[2]) < 2:
        return None
    key, value = call[2][0], call[2][1]
    return key if ident(value) in rows and references(key, keys) else None


def _stores_row(consequence: Node, container: str, rows: frozenset[str]) -> bool:
    """``container[...] = row`` (or a field of it), not a computed value."""
    for node in walk(consequence):
        left = node.child_by_field_name("left") if node.type == "assignment" else None
        right = node.child_by_field_name("right") if left is not None else None
        if (
            left is not None
            and right is not None
            and left.type == "subscript"
            and ident(left.child_by_field_name("value")) == container
            and right.type in ("identifier", "attribute", "subscript")
            and references(right, rows)
        ):
            return True
    return False


def _adds_to(stmts: list[Node], container: str, keys: frozenset[str]) -> bool:
    calls = (_method_call(n) for stmt in stmts for n in walk(stmt) if n.type == "call")
    return any(
        call and call[0] == container and call[1] == "add" and call[2]
        and references(call[2][0], keys)
        for call in calls
    )


def _stores_value(node: Node, values: frozenset[str]) -> bool:
    """``x.add/append/extend(v)`` or ``x[...] = v`` with *v* reading *values*."""
    call = _method_call(node)
    if call and call[1] in ("add", "append", "extend") and any(
        references(arg, values) for arg in call[2]
    ):
        return True
    left = node.child_by_field_name("left") if node.type == "assignment" else None
    right = node.child_by_field_name("right") if left is not None else None
    return (
        left is not None
        and right is not None
        and left.type == "subscript"
        and references(right, values)
    )


def _keeps_every_row(stmts: list[Node], values: frozenset[str]) -> bool:
    """Whether an unguarded statement stores each iterated row."""
    nested_loops = frozenset({"for_statement", "while_statement"})
    guarded = False
    for stmt in stmts:
        candidates = walk(stmt, nested_loops) if stmt.type not in nested_loops else ()
        guarded = guarded or any(n.type == "continue_statement" for n in candidates)
        if guarded:
            continue
        node = next((c for c in stmt.children if c.is_named), stmt)
        if _stores_value(node, values):
            return True
    return False


def _guard_key(
    stmt: Node, rest: list[Node], rows: frozenset[str], keys: frozenset[str]
) -> Node | None:
    guard = _membership(stmt)
    if guard is None or not references(guard[1], keys):
        return None
    operator, key, container, consequence = guard
    if operator == "not in":
        return key if _stores_row(consequence, container, rows) else None
    skips = any(n.type == "continue_statement" for n in walk(consequence))
    return key if skips and _adds_to(rest, container, keys) else None


def reduction_key(for_stmt: Node) -> Node | None:
    """The key this loop keeps one row per, or ``None`` when it keeps no such row."""
    body = for_stmt.child_by_field_name("body")
    rows = _row_names(for_stmt)
    if body is None or not rows:
        return None
    stmts = [c for c in body.children if c.is_named]
    keys = _derived(stmts, rows)
    if _keeps_every_row(stmts, keys):
        return None
    for index, stmt in enumerate(stmts):
        key = _setdefault_key(stmt, rows, keys)
        if key is None:
            key = _guard_key(stmt, stmts[index + 1 :], rows, keys)
        if key is not None:
            return key
    return None


def reduces_per_key(for_stmt: Node) -> bool:
    """Whether this loop keeps one row per key of what it iterates."""
    return reduction_key(for_stmt) is not None


def key_fields(for_stmt: Node, key: Node) -> frozenset[str]:
    """The row fields *key* reads: ``row.repo_id``, ``row["repo_id"]`` or
    ``row.get("repo_id")``, also through one local (``rid = row["repo_id"]``).
    A name unpacked from the row (``for path, name in rows``) is its own field."""
    rows = _row_names(for_stmt)
    local = _locals(for_stmt)
    fields: set[str] = set()
    pending, hopped = [key], False
    while pending:
        for node in walk(pending.pop()):
            name = ident(node)
            parent = node.parent
            if name is None or parent is None:
                continue
            if parent.type == "attribute" and parent.child_by_field_name("attribute") == node:
                continue
            if name in rows:
                fields.add(_field_of(node, parent) or name)
            elif name in local and not hopped:
                pending.append(local[name])
        hopped = True
    return frozenset(fields)


def _locals(for_stmt: Node) -> dict[str, Node]:
    """Each name the loop body assigns, to its first value."""
    body = for_stmt.child_by_field_name("body")
    local: dict[str, Node] = {}
    for stmt in body.named_children if body is not None else ():
        node = next((c for c in stmt.children if c.is_named), stmt)
        if node.type != "assignment":
            continue
        name, right = ident(node.child_by_field_name("left")), node.child_by_field_name("right")
        if name and right is not None:
            local.setdefault(name, right)
    return local


def _field_of(row: Node, parent: Node) -> str | None:
    """``row.f`` / ``row["f"]`` / ``row.get("f")`` -> ``f``."""
    if parent.type == "subscript" and parent.child_by_field_name("value") == row:
        index = parent.child_by_field_name("subscript")
        return _node_text(index).strip("\"'") if index is not None and index.type == "string" else None
    if parent.type != "attribute" or parent.child_by_field_name("object") != row:
        return None
    field = field_text(parent, "attribute")
    call = parent.parent
    if field == "get" and call is not None and call.type == "call":
        args = call.child_by_field_name("arguments")
        first = next((a for a in args.named_children), None) if args is not None else None
        return _node_text(first).strip("\"'") if first is not None and first.type == "string" else None
    return field


__all__ = [
    "field_text",
    "ident",
    "key_fields",
    "reduces_per_key",
    "reduction_key",
    "references",
    "walk",
]
