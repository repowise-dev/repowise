"""What a TypeScript guard above an Extract Method span narrows.

``if (!reply) return;`` narrows ``reply`` from ``Reply | undefined`` to
``Reply`` for the code after it, the span included. A helper parameter typed
with the declared type undoes that, and the lifted code stops compiling where
it relies on it. A guard counts when it exits (``return``, ``throw``,
``continue``, ``break``) with no ``else`` and sits before the span in a block
holding it, and only for a name nothing writes between the guard and the end
of the span.

The arms a test removes are known for ``!x``, ``x == null`` / ``=== null``,
``x == undefined`` / ``=== undefined`` and ``typeof x === "undefined"``,
alone or joined by ``||``. ``typeof``, ``instanceof`` and ``Array.isArray``
narrow to a type the declaration does not name, so those are reported with no
arms: the plan says the parameter needs the narrowed type instead of writing
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..dataflow.dialects.ts_js import union_arms
from ..dataflow.span import exiting_guard, span_statements

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

_FALSY = frozenset({"undefined", "null", "false"})
_NULLISH = frozenset({"undefined", "null"})
_EQUALITY = {"==": _NULLISH, "===": None}
_EXITS = frozenset({"return_statement", "throw_statement", "continue_statement", "break_statement"})


@dataclass(frozen=True)
class Narrowing:
    """The guard's line and the union arms it removes (None: it narrows to a
    type the declaration does not name)."""

    line: int
    drops: frozenset[str] | None


def narrowings(
    fn_node: Node | None,
    lmap: LanguageNodeMap,
    start: int,
    end: int,
    names: set[str],
    def_lines: dict[str, list[int]],
) -> dict[str, Narrowing]:
    """Per name in *names*, the nearest guard narrowing it over lines
    *start*..*end* (module docstring)."""
    stmts = span_statements(fn_node, start, end, lmap)
    out: dict[str, Narrowing] = {}
    node = stmts[0] if stmts else None
    while node is not None and node.parent is not None and node is not fn_node:
        if node.parent.type in lmap.block_kinds:
            sib = node.prev_named_sibling
            while sib is not None:
                _add_guard(sib, lmap, end, names, def_lines, out)
                sib = sib.prev_named_sibling
        node = node.parent
    return out


def _add_guard(
    sib: Node,
    lmap: LanguageNodeMap,
    end: int,
    names: set[str],
    def_lines: dict[str, list[int]],
    out: dict[str, Narrowing],
) -> None:
    """Record in *out* each name in *names* guard *sib* narrows that has no
    nearer guard yet and nothing rebinds before line *end*."""
    line = sib.start_point[0] + 1
    for name, drops in _guard_tests(sib, lmap).items():
        rebound = any(sib.end_point[0] + 1 < ln <= end for ln in def_lines.get(name, ()))
        if name in names and name not in out and not rebound:
            out[name] = Narrowing(line, drops)


def narrowed(declared: str, drops: frozenset[str]) -> str | None:
    """*declared* without the union arms in *drops*; None when nothing is left."""
    arms = union_arms(declared)
    kept = [a for a in arms if a not in drops]
    if len(kept) == len(arms):
        return declared
    return " | ".join(kept) or None


def _guard_tests(st: Node, lmap: LanguageNodeMap) -> dict[str, frozenset[str] | None]:
    """What an exiting ``if`` with no ``else`` proves false about each name."""
    condition = exiting_guard(st, lmap, _EXITS)
    return _false_means(condition) if condition is not None else {}


def _false_means(expr: Node) -> dict[str, frozenset[str] | None]:
    """Per name, the arms *expr* being false removes."""
    expr = _peel(expr)
    op = _operator(expr)
    if expr.type == "binary_expression" and op == "||":
        return _merged(
            _false_means(expr.child_by_field_name("left")),
            _false_means(expr.child_by_field_name("right")),
        )
    if expr.type == "unary_expression" and op == "!":
        arg = _peel(expr.child_by_field_name("argument"))
        if arg.type == "identifier":
            return {_text(arg): _FALSY}
        return dict.fromkeys(_typed_names(arg))
    if expr.type == "binary_expression" and op in _EQUALITY:
        return _equality(expr, op)
    # ``a && b`` being false proves neither side false.
    return {} if op == "&&" else dict.fromkeys(_typed_names(expr))


def _merged(
    a: dict[str, frozenset[str] | None], b: dict[str, frozenset[str] | None]
) -> dict[str, frozenset[str] | None]:
    """Both sides of ``||`` false: a name tested on both loses both sides'
    arms (``a === null || a === undefined``); an unknown narrowing stays one."""
    out = dict(a)
    for name, drops in b.items():
        if name not in out:
            out[name] = drops
        elif out[name] is not None and drops is not None:
            out[name] = out[name] | drops
        else:
            out[name] = None
    return out


def _equality(expr: Node, op: str) -> dict[str, frozenset[str] | None]:
    """``x == null``, ``x === undefined``, ``typeof x === "undefined"``."""
    sides = [_peel(expr.child_by_field_name(f)) for f in ("left", "right")]
    texts = [_text(s) for s in sides]
    for k in (0, 1):
        other, value = sides[k], texts[1 - k]
        if other.type == "identifier" and value in _NULLISH:
            return {_text(other): _EQUALITY[op] or frozenset({value})}
        if _operator(other) == "typeof" and value.strip("'\"`") == "undefined":
            arg = _peel(other.child_by_field_name("argument"))
            if arg.type == "identifier":
                return {_text(arg): frozenset({"undefined"})}
    return dict.fromkeys(_typed_names(expr))


def _typed_names(expr: Node) -> list[str]:
    """Names a ``typeof`` / ``instanceof`` / ``Array.isArray`` test in *expr*
    narrows to a type the declaration does not name."""
    out = []
    stack = [expr]
    while stack:
        node = stack.pop()
        target = None
        if node.type == "unary_expression" and _operator(node) == "typeof":
            target = node.child_by_field_name("argument")
        elif node.type == "binary_expression" and _operator(node) == "instanceof":
            target = node.child_by_field_name("left")
        elif node.type == "call_expression" and _text(node.child_by_field_name("function")) == "Array.isArray":
            args = node.child_by_field_name("arguments")
            target = args.named_children[0] if args is not None and args.named_children else None
        if target is not None and _peel(target).type == "identifier":
            out.append(_text(_peel(target)))
        stack.extend(node.named_children)
    return out


def _peel(node: Node) -> Node:
    while node.type == "parenthesized_expression" and node.named_children:
        node = node.named_children[0]
    return node


def _operator(node: Node) -> str | None:
    op = node.child_by_field_name("operator")
    return _text(op) if op is not None else None


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else ""


__all__ = ["Narrowing", "narrowed", "narrowings"]
