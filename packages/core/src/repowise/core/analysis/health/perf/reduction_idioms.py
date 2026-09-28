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


def _keeps_first_via_setdefault(stmt: Node, rows: frozenset[str], keys: frozenset[str]) -> bool:
    call = _method_call(stmt)
    if call is None or call[1] != "setdefault" or len(call[2]) < 2:
        return False
    key, value = call[2][0], call[2][1]
    return ident(value) in rows and references(key, keys)


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


def _keeps_first_via_guard(
    stmt: Node, rest: list[Node], rows: frozenset[str], keys: frozenset[str]
) -> bool:
    guard = _membership(stmt)
    if guard is None or not references(guard[1], keys):
        return False
    operator, _key, container, consequence = guard
    if operator == "not in":
        return _stores_row(consequence, container, rows)
    skips = any(n.type == "continue_statement" for n in walk(consequence))
    return skips and _adds_to(rest, container, keys)


def reduces_per_key(for_stmt: Node) -> bool:
    """Whether this loop keeps one row per key of what it iterates."""
    body = for_stmt.child_by_field_name("body")
    rows = _row_names(for_stmt)
    if body is None or not rows:
        return False
    stmts = [c for c in body.children if c.is_named]
    keys = _derived(stmts, rows)
    return any(
        _keeps_first_via_setdefault(stmt, rows, keys)
        or _keeps_first_via_guard(stmt, stmts[index + 1 :], rows, keys)
        for index, stmt in enumerate(stmts)
    )


__all__ = ["field_text", "ident", "reduces_per_key", "references", "walk"]
