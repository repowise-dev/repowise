"""How much of a function's decision logic lives in one dispatch on one value.

A ``match`` / ``switch`` / ``when`` over one subject, an ``if`` / ``elif``
chain whose every condition tests the same name, or a run of sibling ``if``
guards that each test it (``if kind == "a": return ...``), is long by
construction: a per-node-type handler adds one arm per case, and the arms are
independent of each other. ``dispatch_share`` lets a reader tell that shape
apart from a function that is complex all over.

A complexity marker reads a function at or above :data:`DISPATCH_SHARE`
through :func:`judged_ccn` and :func:`judged_nesting`: the decision points
outside its one dispatch plus those of its heaviest arm, and its nesting less
the levels the dispatch opens. A per-case handler is flagged when the code
around the dispatch, or one of its arms, is complex on its own.

The share is the decision points inside that one branch, arms and everything
nested in them, over the function's decision points (CCN minus the entry
path). It counts the same nodes :mod:`.cyclomatic` charged, so a flat switch
that CCN charges one point is one point here too. It changes no stored CCN.

"Top level" means not nested inside another branch, case or catch. Loops,
``try`` / ``with`` blocks and closures are walked through, since a visitor's
dispatch usually sits inside the loop that reads the next token, and a
middleware factory's inside the closure it returns.

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
    from .models import FunctionComplexity

#: A function whose largest dispatch on one value holds this share of its
#: decision points is mostly that dispatch. Fitted on the dev labels only:
#: share >= 0.6 held 9 labelled complexity rows, 8 of them rejected.
DISPATCH_SHARE = 0.6
#: Nesting levels a dispatch opens before its arms' own code: a ``switch`` and
#: its ``case``. A same-subject ``if`` chain opens one, so this is the bound.
_DISPATCH_LEVELS = 2

# An ``if`` with one ``elif`` is a decision, not a dispatch; the same holds for
# a run of guards.
_MIN_ARMS = 3

# The ``else`` part of an ``if`` where a grammar gives it no ``alternative`` field.
_ELSE_KINDS = frozenset({"else_clause", "else"})
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
    {"equals", "equalsignorecase", "matches", "is_a?", "kind_of?", "instance_of?", "startswith"}
)
# A receiver that names a constant (``FormatNames.ISO8601.matches(input)``) is
# the arm's key, so the test is on its argument.
_CONSTANT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
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


def _receiver_test_subject(receiver: Node | None, args: list[Node]) -> str | None:
    """``x.equals(y)`` tests ``x``, unless ``x`` is a literal or a constant key."""
    subject = _name(receiver)
    if subject is not None and _CONSTANT_RE.match(subject.rsplit(".", 1)[-1]):
        subject = None
    return subject or (_name(args[0]) if args else None)


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
            return _receiver_test_subject(receiver, args)
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


def _is_chain_head(node: Node) -> bool:
    """An ``if`` that starts a chain rather than continuing one."""
    return node.type in _ELSE_IF_NODE_KINDS and not _is_elif_continuation(node)


def _is_lone_if(node: Node) -> bool:
    """An ``if`` that neither continues nor is continued by an ``elif``."""
    return _is_chain_head(node) and len(_chain_arms(node)) == 1


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


class Dispatch(NamedTuple):
    """A function's largest dispatch: its decision points and its heaviest arm's."""

    points: int = 0
    arm: int = 0


def _points(nodes: list[Node], lmap: LanguageNodeMap) -> int:
    """Decision points the CCN walk charges inside *nodes*."""
    return _walk_function_body(_Siblings(nodes), lmap)[0] - 1  # type: ignore[arg-type]


def _if_arm_points(node: Node, lmap: LanguageNodeMap) -> int:
    """Decision points in one ``if`` arm's body, not its condition or ``else``."""
    skip = {
        c.id
        for c in (node.child_by_field_name("condition"), node.child_by_field_name("alternative"))
        if c is not None
    }
    body = [
        c
        for c in node.named_children
        if c.id not in skip and c.type not in _ELSE_KINDS and not _is_elif_continuation(c)
    ]
    return _points(body, lmap)


def _if_dispatch(nodes: list[Node], arms: list[Node], lmap: LanguageNodeMap) -> Dispatch:
    """A same-subject ``if`` chain (one node) or guard run (its ``if`` nodes)."""
    return Dispatch(_points(nodes, lmap), max(_if_arm_points(a, lmap) for a in arms))


def _switch_dispatch(node: Node, cases: list[Node], lmap: LanguageNodeMap) -> Dispatch:
    """A ``switch`` / ``match``; an arm's own case point is the dispatch's, not the arm's."""
    return Dispatch(_points([node], lmap), max(_points([c], lmap) - 1 for c in cases))


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


def _node_dispatch(node: Node, lmap: LanguageNodeMap) -> Dispatch | None:
    """*node* as a ``switch`` on a subject or the head of a same-subject ``if`` chain."""
    if node.type in lmap.switch_kinds:
        cases = [c for c in _collect_case_children(node, lmap) if c.is_named]
        return _switch_dispatch(node, cases, lmap) if cases and _has_subject(node) else None
    if node.type not in lmap.branch_kinds or not _is_chain_head(node):
        return None
    arms = _chain_arms(node)
    return _if_dispatch([node], arms, lmap) if _one_subject(arms, lmap) else None


def _dispatches(children: list[Node], lmap: LanguageNodeMap) -> list[Dispatch]:
    """Every dispatch among one parent's *children*: guard runs, chains, switches."""
    found = [_if_dispatch(run, run, lmap) for run in _guard_runs(children, lmap)]
    found.extend(d for c in children if (d := _node_dispatch(c, lmap)) is not None)
    return found


def dispatch_points(body: Node, lmap: LanguageNodeMap) -> Dispatch:
    """The largest top-level dispatch on one subject, by decision points."""
    stop_kinds = lmap.case_kinds | lmap.catch_kinds | lmap.function_kinds
    descend_past = stop_kinds | lmap.switch_kinds | lmap.branch_kinds
    best = Dispatch()
    stack: list[Node] = [body]
    while stack:
        children = [c for c in stack.pop().children if c.is_named]
        best = max([best, *_dispatches(children, lmap)])
        stack.extend(c for c in children if c.type not in descend_past)
    return best


def dispatch_share(points: int, ccn: int) -> float:
    """*points* as a fraction of the function's decision points, to two decimals."""
    return round(points / (ccn - 1), 2) if ccn > 1 and points > 0 else 0.0


def judged_ccn(fn: FunctionComplexity) -> int:
    """The CCN a complexity marker judges *fn* by.

    Below :data:`DISPATCH_SHARE` that is its CCN. At or above it, the points
    outside the dispatch plus its heaviest arm's: a switch of one-line cases
    reads as the code around it, one with a tangled arm as that arm.
    """
    if fn.dispatch_share < DISPATCH_SHARE:
        return fn.ccn
    return fn.ccn - round(fn.dispatch_share * (fn.ccn - 1)) + fn.dispatch_arm


def judged_nesting(fn: FunctionComplexity) -> int:
    """The nesting a marker judges *fn* by: less the dispatch's own levels once it dominates.

    The deepest block of a dominated function is almost always in an arm, so
    this subtracts the most a dispatch opens. A block that deep outside the
    dispatch is under-read by the same amount (the ceiling of not tracking
    which side the deepest block is on).
    """
    if fn.dispatch_share < DISPATCH_SHARE:
        return fn.max_nesting
    return max(fn.max_nesting - _DISPATCH_LEVELS, 0)
