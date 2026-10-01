"""How much of a function's decision logic lives in one dispatch on one value.

A ``match`` / ``switch`` / ``when`` over one subject, an ``if`` / ``elif``
chain whose every condition tests the same name, or a run of sibling ``if``
guards that each test it (``if kind == "a": return ...``), is long by
construction: a per-node-type handler adds one arm per case, and the arms are
independent of each other. ``dispatch_share`` lets a reader tell that shape
apart from a function that is complex all over.

The share is the decision points inside that one branch, arms and everything
nested in them, over the function's decision points (CCN minus the entry
path). It counts the same nodes :mod:`.cyclomatic` charged, so a flat switch
that CCN charges one point is one point here too. It is a fact read beside
CCN; it changes no CCN, threshold or score.

"Top level" means not nested inside another branch, case or catch. Loops,
``try`` / ``with`` blocks and closures are walked through, since a visitor's
dispatch usually sits inside the loop that reads the next token, and a
middleware factory's inside the closure it returns.

The 0.6 cut a consumer reads it against was fitted on labelled dev repos (see
the tests); it is not a property of the fact.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from .cyclomatic import (
    _ELSE_IF_NODE_KINDS,
    _collect_case_children,
    _is_boolean_operator,
    _is_elif_continuation,
    _walk_function_body,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from .languages import LanguageNodeMap

# An ``if`` with one ``elif`` is a decision, not a dispatch; the same holds for
# a run of guards.
_MIN_ARMS = 3

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
        # ``kind == "call" and lang == "ruby"`` dispatches on ``kind``: the
        # first side that tests a name is the one the arm is keyed on.
        return _subject(node.child_by_field_name("left"), lmap) or _subject(
            node.child_by_field_name("right"), lmap
        )
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


def _one_subject(arms: list[Node], lmap: LanguageNodeMap) -> bool:
    """Whether every arm's condition tests the same single name."""
    if len(arms) < _MIN_ARMS:
        return False
    subjects = Counter(_subject(arm.child_by_field_name("condition"), lmap) for arm in arms)
    return len(subjects) == 1 and None not in subjects


def _is_lone_if(node: Node) -> bool:
    """An ``if`` that neither continues nor is continued by an ``elif``."""
    return (
        node.type in _ELSE_IF_NODE_KINDS
        and not _is_elif_continuation(node)
        and len(_chain_arms(node)) == 1
    )


def _has_subject(node: Node) -> bool:
    """A subjectless ``switch {}`` / ``when {}`` / ``case`` is an ``if`` chain."""
    if node.type == "expression_switch_statement":
        return node.child_by_field_name("value") is not None
    if node.type == "when_expression":
        return any(c.type == "when_subject" for c in node.children)
    if node.type == "case" and any(c.type == "when" for c in node.children):
        return node.child_by_field_name("value") is not None
    return True


class _Siblings(NamedTuple):
    """A stand-in body holding just the nodes of one dispatch, for the walker."""

    children: list[Node]


def _points(nodes: list[Node], lmap: LanguageNodeMap) -> int:
    """Decision points the CCN walk charges inside *nodes*."""
    return _walk_function_body(_Siblings(nodes), lmap)[0] - 1  # type: ignore[arg-type]


def _guard_runs(children: list[Node], lmap: LanguageNodeMap) -> list[list[Node]]:
    """Runs of three or more consecutive sibling ``if`` guards on one subject."""
    runs: list[list[Node]] = []
    run: list[Node] = []
    subject: str | None = None
    for child in [*children, None]:
        this = (
            _subject(child.child_by_field_name("condition"), lmap)
            if child is not None and _is_lone_if(child)
            else None
        )
        if this is not None and this == subject:
            run.append(child)  # type: ignore[arg-type]
            continue
        if len(run) >= _MIN_ARMS:
            runs.append(run)
        run, subject = ([child] if this is not None else []), this  # type: ignore[list-item]
    return runs


def dispatch_points(body: Node, lmap: LanguageNodeMap) -> int:
    """Decision points inside the largest top-level dispatch on one subject."""
    stop_kinds = lmap.case_kinds | lmap.catch_kinds | lmap.function_kinds
    best = 0
    stack: list[Node] = [body]
    while stack:
        parent = stack.pop()
        children = [c for c in parent.children if c.is_named]
        for run in _guard_runs(children, lmap):
            best = max(best, _points(run, lmap))
        for node in children:
            if node.type in lmap.switch_kinds:
                if _has_subject(node) and any(
                    c.is_named for c in _collect_case_children(node, lmap)
                ):
                    best = max(best, _points([node], lmap))
                continue
            if node.type in lmap.branch_kinds:
                if (
                    node.type in _ELSE_IF_NODE_KINDS
                    and not _is_elif_continuation(node)
                    and _one_subject(_chain_arms(node), lmap)
                ):
                    best = max(best, _points([node], lmap))
                continue
            if node.type not in stop_kinds:
                stack.append(node)
    return best


def dispatch_share(points: int, ccn: int) -> float:
    """*points* as a fraction of the function's decision points, to two decimals."""
    return round(points / (ccn - 1), 2) if ccn > 1 and points > 0 else 0.0
