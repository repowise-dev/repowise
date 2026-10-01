"""How much of a function's CCN is one dispatch on one value.

A ``match`` / ``switch`` / ``when`` over one subject, or an ``if`` / ``elif``
chain whose every condition tests the same name, is long by construction: a
token visitor or a per-node-type handler adds one arm per case it handles, and
the arms are usually the clearest way to write it. ``dispatch_share`` lets a
reader tell that shape apart from a function that is complex all over.

It is a fact about the function, read beside CCN; it changes no CCN, no
threshold and no score. The count mirrors what :mod:`.cyclomatic` charged the
same nodes, so the share is a true fraction of the CCN it is divided by:

- a ``switch`` with arms charges one point per arm, a *flat* one (every arm a
  single expression) one point in total;
- an ``if`` chain charges one point per ``if`` / ``elif`` arm, plus each
  boolean operator in those arms' conditions.

"Top level" means not nested inside another branch, case or catch. Loops,
``try`` and ``with`` blocks are walked through, since a visitor's dispatch
usually sits inside the loop that reads the next token.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .cyclomatic import (
    _ELSE_IF_NODE_KINDS,
    _collect_case_children,
    _count_boolean_ops_in_condition,
    _is_boolean_operator,
    _is_elif_continuation,
    _is_flat_match,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from .languages import LanguageNodeMap

# An ``if`` with one ``elif`` is a decision, not a dispatch.
_MIN_CHAIN_ARMS = 3

_WRAPPER_KINDS = frozenset({"parenthesized_expression", "condition_clause"})
_COMPARISON_KINDS = frozenset(
    {
        "comparison_operator",
        "binary_expression",
        "binary",
        "instanceof_expression",
        "is_expression",
        "is_pattern_expression",
        "check_expression",
        "equality_expression",
    }
)
# Calls that test their first argument's type or value.
_TYPE_TEST_FUNCTIONS = frozenset({"isinstance", "issubclass", "type", "hasattr"})
# Methods that test their receiver.
_RECEIVER_TESTS = frozenset(
    {"equals", "equalsignorecase", "is_a?", "kind_of?", "instance_of?", "startswith"}
)
_LITERAL_MARKERS = ("string", "integer", "number", "float", "true", "false", "null", "nil", "char")
_MAX_SUBJECT_CHARS = 80
_SPACE_RE = re.compile(r"\s+")


def _unwrap(node: Node | None) -> Node | None:
    while node is not None and node.type in _WRAPPER_KINDS:
        named = node.named_children
        if len(named) != 1:
            return node
        node = named[0]
    return node


def _name(node: Node | None) -> str | None:
    """The subject text of an operand, ``None`` for a literal or a sprawl."""
    node = _unwrap(node)
    if node is None or node.text is None:
        return None
    if node.type == "unary_expression" and node.child_by_field_name("argument") is not None:
        # ``typeof x`` tests ``x``.
        return _name(node.child_by_field_name("argument"))
    kind = node.type.lower()
    if kind in ("none", "true", "false") or any(m in kind for m in _LITERAL_MARKERS):
        return None
    text = node.text.decode("utf-8", errors="replace")
    if "\n" in text or len(text) > _MAX_SUBJECT_CHARS:
        return None
    return _SPACE_RE.sub("", text)


def _call_parts(node: Node) -> tuple[str, Node | None, list[Node]]:
    """``(method name, receiver, arguments)`` for a call node, best effort."""
    receiver = node.child_by_field_name("object") or node.child_by_field_name("receiver")
    callee = node.child_by_field_name("function") or node.child_by_field_name("method")
    name_node = node.child_by_field_name("name") or callee
    if callee is not None and callee.type in ("attribute", "member_expression"):
        receiver = callee.child_by_field_name("object")
        name_node = callee.child_by_field_name("attribute") or callee.child_by_field_name(
            "property"
        )
    name = (name_node.text or b"").decode("utf-8", errors="replace") if name_node else ""
    args_node = node.child_by_field_name("arguments")
    args = list(args_node.named_children) if args_node is not None else []
    return name.rsplit(".", 1)[-1].lower(), receiver, args


def _subject(node: Node | None, lmap: LanguageNodeMap) -> str | None:
    """The one name a condition tests, ``None`` when it tests no single one."""
    node = _unwrap(node)
    if node is None:
        return None
    if _is_boolean_operator(node, lmap):
        sides = [node.child_by_field_name("left"), node.child_by_field_name("right")]
        if None in sides:
            return None
        subjects = {_subject(side, lmap) for side in sides}
        return subjects.pop() if len(subjects) == 1 else None
    if node.type in _COMPARISON_KINDS:
        named = node.named_children
        left = node.child_by_field_name("left") or (named[0] if named else None)
        right = node.child_by_field_name("right") or (named[-1] if named else None)
        return _name(left) or _name(right)
    if node.type in lmap.call_kinds:
        name, receiver, args = _call_parts(node)
        if name in _TYPE_TEST_FUNCTIONS and receiver is None and args:
            return _name(args[0])
        if name in _RECEIVER_TESTS:
            return _name(receiver) or (_name(args[0]) if args else None)
    return None


def _chain_arms(node: Node) -> list[Node]:
    """The ``if`` and every ``elif`` / ``else if`` arm that continues it."""
    arms = [node]
    current = node
    while True:
        continuations: list[Node] = []
        for child in current.children:
            candidates = child.named_children if child.type == "else_clause" else [child]
            continuations.extend(c for c in candidates if _is_elif_continuation(c))
        if not continuations:
            return arms
        arms.extend(continuations)
        current = continuations[-1]


def _chain_points(node: Node, lmap: LanguageNodeMap) -> int:
    if node.type not in _ELSE_IF_NODE_KINDS:
        return 0
    arms = _chain_arms(node)
    if len(arms) < _MIN_CHAIN_ARMS:
        return 0
    conditions = [arm.child_by_field_name("condition") for arm in arms]
    if any(c is None for c in conditions):
        return 0
    subjects = {_subject(c, lmap) for c in conditions}
    if len(subjects) != 1 or None in subjects:
        return 0
    return len(arms) + sum(_count_boolean_ops_in_condition(c, lmap) for c in conditions)


def _has_subject(node: Node) -> bool:
    """A subjectless ``switch {}`` / ``when {}`` / ``case`` is an ``if`` chain."""
    if node.type == "expression_switch_statement":
        return node.child_by_field_name("value") is not None
    if node.type == "when_expression":
        return any(c.type == "when_subject" for c in node.children)
    if node.type == "case" and any(c.type == "when" for c in node.children):
        return node.child_by_field_name("value") is not None
    return True


def _switch_points(node: Node, lmap: LanguageNodeMap) -> int:
    if not _has_subject(node):
        return 0
    arms = [c for c in _collect_case_children(node, lmap) if c.is_named]
    if not arms:
        return 0
    return 1 if _is_flat_match(node, lmap) else len(arms)


def dispatch_points(body: Node, lmap: LanguageNodeMap) -> int:
    """CCN points of the largest top-level multiway branch on one subject."""
    stop_kinds = lmap.case_kinds | lmap.catch_kinds | lmap.function_kinds | lmap.lambda_kinds
    best = 0
    stack: list[Node] = list(body.children)
    while stack:
        node = stack.pop()
        if not node.is_named:
            continue
        if node.type in lmap.switch_kinds:
            best = max(best, _switch_points(node, lmap))
            continue
        if node.type in lmap.branch_kinds:
            best = max(best, _chain_points(node, lmap))
            continue
        if node.type in stop_kinds:
            continue
        stack.extend(node.children)
    return best


def dispatch_share(points: int, ccn: int) -> float:
    """*points* as a fraction of *ccn*, to two decimals."""
    return round(points / ccn, 2) if ccn > 0 and points > 0 else 0.0
