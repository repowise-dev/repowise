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

Separately, a run of error guards (Go ``if err != nil { return err }``, C
``if (rc) goto cleanup;``, shell ``cmd || die``) counts once in
:func:`judged_ccn`, and so does a run of guarded copies
(``if (m.containsKey(k)) o.f = m.get(k);``); when both cuts apply, the
smaller reading wins.

The share is the decision points inside that one branch, arms and everything
nested in them, over the function's decision points (CCN minus the entry
path). It counts the same nodes :mod:`.cyclomatic` charged, so a flat switch
that CCN charges one point is one point here too. It changes no stored CCN.

"Top level" means not nested inside another branch, case or catch. Loops,
``try`` / ``with`` blocks and closures are walked through, since a visitor's
dispatch usually sits inside the loop that reads the next token, and a
middleware factory's inside the closure it returns. A block-bodied arrow bound
to a name is not: it is a unit of its own, with its own dispatch.

"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from .ast_utils import is_named_block_lambda
from .cyclomatic import (
    _ELSE_IF_NODE_KINDS,
    _collect_case_children,
    _has_subject,
    _is_boolean_operator,
    _is_elif_continuation,
    _walk_function_body,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node

    from .languages import LanguageNodeMap
    from .models import FunctionComplexity

#: A function whose largest dispatch on one value holds this share of its
#: decision points is mostly that dispatch. Fitted on the dev labels only:
#: share >= 0.6 held 9 labelled complexity rows, 8 of them rejected.
DISPATCH_SHARE = 0.6
#: Nesting levels taken off a dominated function. A dispatch opens one (a
#: ``switch`` or a same-subject ``if`` chain); the second covers the loop a
#: per-case handler usually sits in. At 1, dispatch-in-a-loop parsers re-flag.
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
        "type_test_expression",
    }
)
# Calls that test their first non-literal argument's type or value: Python's
# type tests, C's string compares (``!strcasecmp(u, "kb")``), PHP's ``is_*``.
_TYPE_TEST_FUNCTIONS = frozenset(
    {
        "isinstance",
        "issubclass",
        "type",
        "hasattr",
        "strcmp",
        "strncmp",
        "strcasecmp",
        "strncasecmp",
        "memcmp",
        "wcscmp",
        "gettype",
        "get_class",
        "is_null",
        "is_scalar",
        "is_object",
        "is_array",
        "is_string",
        "is_int",
        "is_integer",
        "is_float",
        "is_bool",
        "is_numeric",
        "is_callable",
        "is_iterable",
        "is_resource",
    }
)
# Methods that test their receiver (``[value isKindOfClass:...]`` too).
_RECEIVER_TESTS = frozenset(
    {
        "equals",
        "equalsignorecase",
        "matches",
        "is_a?",
        "kind_of?",
        "instance_of?",
        "startswith",
        "hasprefix",
        "iskindofclass",
        "ismemberofclass",
        "isequal",
        "isequaltostring",
    }
)
_NOT_OPERATORS = frozenset({"!", "not"})
_ORDERING_OPERATORS = frozenset({"<", ">", "<=", ">="})
_DART_OPERATOR_KINDS = frozenset({"equality_operator", "relational_operator", "type_test"})
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
    if node.type == "pointer_expression" and node.children and node.children[0].type == "*":
        # ``*u == '\0'`` tests the string ``u`` as ``strcasecmp(u, "b")`` does.
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
    # ``strings.HasPrefix`` and PHP's ``\is_object`` name the function last.
    return re.split(r"[.\\]", name)[-1].lower(), receiver, args


def _receiver_test_subject(receiver: Node | None, args: list[Node]) -> str | None:
    """``x.equals(y)`` tests ``x``, unless ``x`` is a literal or a constant key."""
    subject = _name(receiver)
    if subject is not None and _CONSTANT_RE.match(subject.rsplit(".", 1)[-1]):
        subject = None
    return subject or (_name(args[0]) if args else None)


def _call_subject(node: Node | None, lmap: LanguageNodeMap) -> str | None:
    """The name a predicate call tests (``isinstance(x, A)``, ``s.equals(t)``), else ``None``."""
    if node is None or node.type not in lmap.call_kinds:
        return None
    name, receiver, args = _call_parts(node)
    if name in _TYPE_TEST_FUNCTIONS and receiver is None:
        return next((s for a in args if (s := _name(a)) is not None), None)
    if name in _RECEIVER_TESTS:
        return _receiver_test_subject(receiver, args)
    return None


def _operand(node: Node | None, lmap: LanguageNodeMap) -> str | None:
    """A compared side's subject: ``strcmp(u, "k") == 0`` compares ``u``."""
    node = _unwrap(node)
    return _call_subject(node, lmap) or _name(node)


def _negated(node: Node) -> Node | None:
    """The operand of ``!x`` / ``not x``, ``None`` for any other node."""
    first = node.children[0] if node.children else None
    if first is None or first.type not in _NOT_OPERATORS or len(node.named_children) != 1:
        return None
    return node.named_children[0]


def _condition(arm: Node) -> Node | None:
    """An ``if`` arm's test; Dart's grammar names no field, so the node inside its parentheses."""
    cond = arm.child_by_field_name("condition")
    if cond is not None:
        return cond
    kids = arm.children
    return next(
        (
            kids[i + 1]
            for i in range(len(kids) - 2)
            if kids[i].type == "(" and kids[i + 2].type == ")"
        ),
        None,
    )


def _tests(node: Node | None, lmap: LanguageNodeMap) -> list[tuple[str, bool]]:
    """Every ``(name, by an ordering operator)`` a condition tests, left side first."""
    node = _unwrap(node)
    if node is None:
        return []
    if (operand := _negated(node)) is not None:
        return _tests(operand, lmap)
    if _is_boolean_operator(node, lmap):
        return _tests(node.child_by_field_name("left"), lmap) + _tests(
            node.child_by_field_name("right"), lmap
        )
    subject = _test_subject(node, lmap)
    return [(subject, _is_ordering(node))] if subject is not None else []


def _is_ordering(node: Node) -> bool:
    """``len > 0``: a bound check, not a test of which value ``len`` is."""
    return node.type in _COMPARISON_KINDS and any(
        c.type in _ORDERING_OPERATORS for c in node.children
    )


def _test_subject(node: Node, lmap: LanguageNodeMap) -> str | None:
    """The name one comparison or predicate call tests."""
    if node.type not in _COMPARISON_KINDS:
        return _call_subject(node, lmap)
    named = node.named_children
    if (selected := _selector_operand(named)) is not None:
        return selected
    left = node.child_by_field_name("left") or (named[0] if named else None)
    right = node.child_by_field_name("right") or (named[-1] if named else None)
    return _operand(left, lmap) or _operand(right, lmap)


def _selector_operand(named: list[Node]) -> str | None:
    """Dart spells ``dep.source is X`` as ``dep``, a ``.source`` selector, then the test."""
    split = next((i for i, c in enumerate(named) if c.type in _DART_OPERATOR_KINDS), 0)
    if split < 2:
        return None
    text = _SPACE_RE.sub(
        "", "".join((c.text or b"").decode("utf-8", "replace") for c in named[:split])
    )
    return text if len(text) <= _MAX_SUBJECT_CHARS else None


def _subject(node: Node | None, lmap: LanguageNodeMap) -> str | None:
    """The name a condition is keyed on, ``None`` when it tests none.

    ``kind == "call" and lang == "ruby"`` dispatches on ``kind``: the first
    side that tests a name is the one the arm is keyed on.
    """
    return next((n for n, _ in _tests(node, lmap)), None)


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
    """Whether every arm's condition tests one same name.

    ``argc == 2 && !strcmp(argv[1], "help")`` then ``!strcmp(argv[1], "load")
    && argc >= 3`` are both keyed on ``argv[1]``, whichever side names it first.
    A name only bound-checked (``len > 0 && x == 1``) keys the chain when no
    arm tests any value or kind (``x < 10``, ``x < 20``, ...).
    """
    if len(arms) < _MIN_ARMS:
        return False
    tests = [_tests(_condition(arm), lmap) for arm in arms]
    shared = set.intersection(*({n for n, _ in t} for t in tests))
    valued = {n for t in tests for n, ordering in t if not ordering}
    return bool(shared & valued) if valued else bool(shared)


def _is_chain_head(node: Node) -> bool:
    """An ``if`` that starts a chain rather than continuing one."""
    return node.type in _ELSE_IF_NODE_KINDS and not _is_elif_continuation(node)


def _is_lone_if(node: Node) -> bool:
    """An ``if`` that neither continues nor is continued by an ``elif``."""
    return _is_chain_head(node) and len(_chain_arms(node)) == 1


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
        c.id for c in (_condition(node), node.child_by_field_name("alternative")) if c is not None
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
            _subject(_condition(child), lmap) if child is not None and _is_lone_if(child) else None
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
    lambda_kinds = lmap.lambda_kinds
    best = Dispatch()
    stack: list[Node] = [body]
    while stack:
        children = [c for c in stack.pop().children if c.is_named]
        best = max([best, *_dispatches(children, lmap)])
        # A named block-bodied arrow is a unit of its own, as the CCN walk
        # reads it, so its dispatches are its own too.
        stack.extend(
            c
            for c in children
            if c.type not in descend_past
            and not (c.type in lambda_kinds and is_named_block_lambda(c, lmap))
        )
    return best


# An early exit that only hands an error up: Go ``if err != nil { return err }``,
# C ``if (rc) goto cleanup;`` and shell ``cmd || die``. A run of them reads as
# one error path, not as one decision each.
_ERROR_NAME_RE = re.compile(r"^(?!std)\w*err\d*$", re.IGNORECASE)
_GOTO_KINDS = frozenset({"goto_statement"})
# Jumps and labels end a run: a guard after one is on another path.
_RUN_BREAK_KINDS = frozenset({"labeled_statement", "goto_statement"})
_SHELL_EXITS = frozenset({b"exit", b"return"})
_SHELL_ERROR_COMMANDS = frozenset({b"die", b"fail", b"fatal", b"abort", b"bail"})


def _statements(node: Node | None, lmap: LanguageNodeMap) -> list[Node]:
    """A branch body's statements, through Go's ``block`` / ``statement_list``."""
    while node is not None and node.type in lmap.block_kinds:
        named = [c for c in node.named_children if "comment" not in c.type]
        if len(named) != 1 or named[0].type not in lmap.block_kinds:
            return named
        node = named[0]
    return [node] if node is not None else []


def _tests_error(node: Node) -> bool:
    """``<name>err != nil``: the condition Go writes before handing an error up."""
    cond = _unwrap(node.child_by_field_name("condition"))
    if cond is None or cond.type != "binary_expression":
        return False
    operator = cond.child_by_field_name("operator")
    right = cond.child_by_field_name("right")
    subject = _name(cond.child_by_field_name("left")) or ""
    return (
        operator is not None
        and operator.text == b"!="
        and right is not None
        and right.type == "nil"
        and _ERROR_NAME_RE.search(subject) is not None
    )


def _enclosing_function(node: Node, lmap: LanguageNodeMap) -> Node:
    """The function holding *node*, or the tree's root outside one."""
    while node.parent is not None and node.type not in lmap.function_kinds:
        node = node.parent
    return node


def _jumps_forward(goto: Node, lmap: LanguageNodeMap) -> bool:
    """A ``goto`` to a label further down its function (``goto cleanup``).

    One that jumps back (``goto again``) is a loop, not an exit.
    """
    target = goto.named_children[-1].text if goto.named_children else None
    stack = [_enclosing_function(goto, lmap)]
    while stack:
        node = stack.pop()
        if node.type == "labeled_statement":
            label = node.child_by_field_name("label")
            if label is not None and label.text == target:
                return node.start_byte > goto.start_byte
        stack.extend(node.named_children)
    return False


def _is_exit_if(node: Node, lmap: LanguageNodeMap) -> bool:
    """An ``if`` with no ``else`` whose body ends in a ``goto`` or an error ``return``."""
    if node.type not in lmap.if_kinds or node.child_by_field_name("alternative") is not None:
        return False
    body = _statements(node.child_by_field_name("consequence"), lmap)
    if not body:
        return False
    if body[-1].type in _GOTO_KINDS:
        return _jumps_forward(body[-1], lmap)
    return body[-1].type in lmap.return_kinds and _tests_error(node)


def _is_shell_exit(node: Node) -> bool:
    """``cmd || exit 1`` / ``[ -f x ] && die``: one operator, then a failing exit.

    ``exit 0`` and ``cmd && return`` leave on success: an early finish, not an error.
    """
    if node.type != "list" or len(node.named_children) != 2:
        return False
    last = node.named_children[-1]
    if last.type == "compound_statement" and last.named_children:
        last = last.named_children[-1]
    name = last.child_by_field_name("name") if last.type == "command" else None
    if name is None:
        return False
    return name.text in _SHELL_ERROR_COMMANDS or _exits_failing(node, last, name)


def _exits_failing(node: Node, command: Node, name: Node) -> bool:
    """``exit`` / ``return`` with a non-zero status, or bare after ``||`` (the failed one)."""
    if name.text not in _SHELL_EXITS:
        return False
    status = command.children_by_field_name("argument")
    if status:
        return status[0].text != b"0"
    return any(c.type == "||" for c in node.children)


def _is_exit_guard(node: Node, lmap: LanguageNodeMap) -> bool:
    """A one-point early exit that hands an error up, with nothing branching inside."""
    return (_is_exit_if(node, lmap) or _is_shell_exit(node)) and _points([node], lmap) == 1


# A guarded copy: ``if (m.containsKey(k)) o.f = m.get(k);`` or
# ``if (x.F != null) o.F = x.F;``. One short statement that uses the value its
# condition tested. A run of them maps fields across, one step, not one
# decision per field.
# Go and F# write an assignment as a statement of its own, Rust a block's last
# expression without the ``;``, F# a call as a bare application.
_ASSIGNMENT_KINDS = frozenset(
    {"assignment", "assignment_expression", "assignment_statement", "mutate_expression"}
)
_COPY_STATEMENT_KINDS = _ASSIGNMENT_KINDS | {
    "expression_statement",
    "call_expression",
    "application_expression",
}
# A statement wrapped over more lines is doing more than one copy (a builder
# call, a map literal), so it is a decision of its own.
_MAX_COPY_LINES = 2
# A name or field tested for truth: Go ``if config.DryRun``, F# ``if x.LoadUserProfile``.
_ACCESS_KINDS = frozenset(
    {
        "identifier",
        "selector_expression",
        "attribute",
        "field_expression",
        "field_access",
        "member_access_expression",
        "member_expression",
        "long_identifier_or_op",
    }
)
_CONJUNCTIONS = frozenset({b"&&", b"and"})
_WORD_RE = re.compile(r"\w+")
_PLAIN_ASSIGN = frozenset({b"=", b"<-"})
_EQUALITY = frozenset({b"==", b"===", b"is"})


def _call_values(call: Node) -> list[Node]:
    """A one-argument call's argument, else a no-argument call's receiver:
    ``m.containsKey(k)`` tests ``k``, ``x.isPresent()`` tests ``x``.

    A call with more arguments (``lua_isstring(L, -1)``) shares a handle with
    whatever it guards, so it names no single tested value.
    """
    if call.type == "application_expression":
        # F# ``isNull x.Domain``: function, then its one argument.
        return call.named_children[1:] if len(call.named_children) == 2 else []
    _, receiver, args = _call_parts(call)
    if len(args) == 1:
        # C# wraps each argument in an ``argument`` node.
        arg = args[0]
        return [arg.named_children[0] if arg.type == "argument" and arg.named_children else arg]
    return [receiver] if not args and receiver is not None else []


def _fsharp_not_operand(node: Node) -> Node | None:
    """The operand of F# ``not (isNull x)``, else ``None``.

    A negated test in other languages (``if (!ok(x)) fail(x)``) handles an
    absent value; it is no setter.
    """
    named = node.named_children
    if node.type == "application_expression" and len(named) == 2 and named[0].text == b"not":
        return named[1]
    return None


def _is_null(node: Node) -> bool:
    kind = node.type.lower()
    return any(m in kind for m in ("null", "nil", "none"))


def _compared_values(comparison: Node) -> list[Node]:
    """``x != null`` tests ``x``; Python ``"k" in opts`` tests ``opts``.

    A bound (``if (start < 0) start = 0``) is arithmetic, not presence.
    """
    sides = comparison.named_children
    if len(sides) != 2:
        return []
    if any(c.type == "in" for c in comparison.children):
        return [sides[1]]
    return [s for s in sides if not _is_null(s)] if sum(map(_is_null, sides)) == 1 else []


def _bound_names(let: Node) -> list[Node]:
    """Rust ``if let Some(v) = opt`` tests what it binds, ``v``.

    ``if let Err(e) = f()`` handles a failure; it tests no optional value.
    """
    pattern = let.child_by_field_name("pattern")
    if pattern is None or pattern.type != "tuple_struct_pattern":
        return []
    names = pattern.named_children
    if not names or names[0].text != b"Some":
        return []
    return [n for n in names[1:] if n.type == "identifier"]


def _tested_values(condition: Node | None, lmap: LanguageNodeMap) -> list[Node]:
    """What a presence test tests: a one-argument call's argument (a
    no-argument call's receiver), the non-null side of a null comparison, a
    let-binding's names, or a bare field tested for truth (``if cfg.DryRun``).
    Anything else tests nothing here."""
    condition = _unwrap(condition)
    if condition is not None and condition.type == "paren_expression":
        # F# parenthesises without a wrapper of its own.
        return _tested_values(condition.named_children[0], lmap) if condition.named_children else []
    if condition is None:
        return []
    negated = _fsharp_not_operand(condition)
    if negated is not None:
        return _tested_values(negated, lmap)
    if condition.type in lmap.call_kinds:
        return _call_values(condition)
    if condition.type in _COMPARISON_KINDS:
        return _compared_values(condition)
    if condition.type == "let_condition":
        return _bound_names(condition)
    return [condition] if condition.type in _ACCESS_KINDS else []


def _presence_tests(condition: Node | None, lmap: LanguageNodeMap) -> list[list[Node]]:
    """The values each presence test in *condition* tests, one list per test.

    ``a.getX() == null && b.getX() != null`` is two tests. Empty when any part
    of the condition is something else (an ``||``, a bound, a computation).
    """
    condition = _unwrap(condition)
    if condition is None:
        return []
    if _is_boolean_operator(condition, lmap):
        if not any(c.text in _CONJUNCTIONS for c in condition.children if not c.is_named):
            return []
        sides = condition.named_children
        left, right = (_presence_tests(s, lmap) for s in (sides[0], sides[-1]))
        return left + right if left and right and len(sides) == 2 else []
    values = _tested_values(condition, lmap)
    return [values] if values else []


def _reads(statement: Node, value: Node) -> bool:
    """Whether *statement* reads an expression spelled like *value*.

    One the statement starts with is its target (``prev.next = next``, a
    null-guarded write through ``prev``), not the value being copied.
    """
    stack = [statement]
    while stack:
        cur = stack.pop()
        if (
            cur.type == value.type
            and cur.text == value.text
            and cur.start_byte != statement.start_byte
        ):
            return True
        stack.extend(cur.named_children)
    return False


def _plain_target(statement: Node) -> Node | None:
    """The target of a plain ``=`` / ``<-`` assignment, ``None`` for anything
    else (a call, a compound ``|=`` that transforms the value)."""
    if statement.type == "expression_statement" and statement.named_child_count == 1:
        statement = statement.named_children[0]
    if statement.type not in _ASSIGNMENT_KINDS or not statement.named_children:
        return None
    plain = any(c.text in _PLAIN_ASSIGN for c in statement.children if not c.is_named)
    return statement.named_children[0] if plain else None


def _sets_mirror(target: Node | None, value: Node) -> bool:
    """Whether *target* is another object's field named like *value*:
    ``if config.DryRun { tx.DryRun = true }``."""
    if target is None or _name(target) == _name(value):
        return False
    names = _WORD_RE.findall((target.text or b"").decode("utf-8", "replace"))
    tested = _WORD_RE.findall((value.text or b"").decode("utf-8", "replace"))
    return bool(names and tested) and names[-1] == tested[-1]


def _is_fill(condition: Node | None, target: str | None) -> bool:
    """Whether *condition* has ``target == null`` / ``target is None`` among
    its ``&&`` operands: the test before filling in a default."""
    stack = [condition]
    while stack:
        node = _unwrap(stack.pop())
        if node is None:
            continue
        tokens = [c.text for c in node.children if not c.is_named]
        if any(t in _CONJUNCTIONS for t in tokens):
            stack.extend(node.named_children)
        elif any(t in _EQUALITY for t in tokens) and target in map(_name, node.named_children):
            return True
    return False


def _lone_if(node: Node, lmap: LanguageNodeMap) -> Node | None:
    """*node* as an ``if`` with no ``else``, through Rust's statement wrapper."""
    if node.type == "expression_statement" and node.named_child_count == 1:
        node = node.named_children[0]
    if node.type not in lmap.if_kinds:
        return None
    if node.child_by_field_name("alternative") or node.child_by_field_name("else"):
        return None
    return node


def _copy_statement(node: Node, lmap: LanguageNodeMap) -> Node | None:
    """An ``if``'s body when it is one short statement (F# names it ``then``)."""
    branch = node.child_by_field_name("consequence") or node.child_by_field_name("then")
    body = _statements(branch, lmap)
    if len(body) != 1 or body[0].type not in _COPY_STATEMENT_KINDS:
        return None
    inner = body[0].named_children[0] if body[0].named_child_count == 1 else body[0]
    if inner.type in lmap.return_kinds | lmap.break_kinds | lmap.continue_kinds:
        # Rust wraps ``return Err(e);`` in an expression statement.
        return None
    if body[0].end_point[0] - body[0].start_point[0] >= _MAX_COPY_LINES:
        return None
    return body[0]


def _guard_parts(node: Node, lmap: LanguageNodeMap) -> tuple[Node, Node, list[Node]] | None:
    """``(condition, statement, tested values)`` of an ``if`` with no ``else``,
    one short statement and nothing but presence tests in its condition."""
    found = _lone_if(node, lmap)
    statement = _copy_statement(found, lmap) if found is not None else None
    if found is None or statement is None:
        return None
    condition = found.child_by_field_name("condition") or found.child_by_field_name("guard")
    tests = _presence_tests(condition, lmap)
    if not tests or len(tests) != _points([found], lmap):
        return None
    # A literal (``-1``, ``"k"``) is no value of its own to copy.
    return condition, statement, [v for test in tests for v in test if _name(v)]


def _is_copy_guard(node: Node, lmap: LanguageNodeMap) -> bool:
    """An ``if`` with no ``else`` whose condition only tests presence and whose
    one statement copies a tested value, or sets the field it is named after."""
    parts = _guard_parts(node, lmap)
    if parts is None:
        return False
    condition, statement, values = parts
    target = _plain_target(statement)
    if target is not None and _name(target) in set(map(_name, values)):
        # ``if x is None: x = {}`` fills a default; ``if flag: flag = False``
        # or ``if z: z = z + 1`` decides what the value becomes.
        return _is_fill(condition, _name(target))
    return any(_reads(statement, v) or _sets_mirror(target, v) for v in values)


def _repeated_guards(
    children: list[Node], lmap: LanguageNodeMap, is_guard: Callable[[Node, LanguageNodeMap], bool]
) -> int:
    """Decision points of the guards among *children* past the first of their run.

    Statements without a decision point (``x, err := f()``) may sit between
    the guards of one run; anything that branches, jumps or is a label ends it.
    """
    breaks = _RUN_BREAK_KINDS | lmap.return_kinds | lmap.break_kinds | lmap.continue_kinds
    repeats = 0
    in_run = False
    for child in children:
        if is_guard(child, lmap):
            # A guarded fill tests two values (``a == null && b != null``).
            repeats += _points([child], lmap) if in_run else 0
            in_run = True
        elif child.type in breaks or _points([child], lmap) > 0:
            in_run = False
    return repeats


def exit_guard_repeats(body: Node, lmap: LanguageNodeMap) -> int:
    """Decision points of error guards that repeat one already counted in their run."""
    return _guard_repeats(body, lmap, _is_exit_guard)


def copy_guard_repeats(body: Node, lmap: LanguageNodeMap) -> int:
    """Decision points of guarded copies that repeat one already counted in their run."""
    return _guard_repeats(body, lmap, _is_copy_guard)


def _run_children(node: Node) -> list[Node]:
    """*node*'s named children, with F#'s right-nested ``sequential_expression``
    spliced flat so the statements of one body read as siblings."""
    children: list[Node] = []
    for child in node.named_children:
        if child.type == "sequential_expression":
            children.extend(_run_children(child))
        else:
            children.append(child)
    return children


def _guard_repeats(
    body: Node, lmap: LanguageNodeMap, is_guard: Callable[[Node, LanguageNodeMap], bool]
) -> int:
    lambda_kinds = lmap.lambda_kinds
    total = 0
    stack: list[Node] = [body]
    while stack:
        children = _run_children(stack.pop())
        total += _repeated_guards(children, lmap, is_guard)
        stack.extend(
            c
            for c in children
            if c.type not in lmap.function_kinds
            and not (c.type in lambda_kinds and is_named_block_lambda(c, lmap))
        )
    return total


def dispatch_share(points: int, ccn: int) -> float:
    """*points* as a fraction of the function's decision points, to two decimals."""
    return round(points / (ccn - 1), 2) if ccn > 1 and points > 0 else 0.0


def judged_ccn(fn: FunctionComplexity) -> int:
    """The CCN a complexity marker judges *fn* by.

    Below :data:`DISPATCH_SHARE` that is its CCN. At or above it, the points
    outside the dispatch plus its heaviest arm's: a switch of one-line cases
    reads as the code around it, one with a tangled arm as that arm.

    A run of error guards, or of guarded copies, counts once either way. The
    cuts can cover the same guards (three ``if err != nil`` in a row are a
    dispatch on ``err`` too), so the smaller reading wins rather than both applying.
    """
    unguarded = fn.ccn - fn.exit_guards - fn.copy_guards
    if fn.dispatch_share < DISPATCH_SHARE:
        return unguarded
    return min(unguarded, fn.ccn - round(fn.dispatch_share * (fn.ccn - 1)) + fn.dispatch_arm)


def judged_leaves_out(fn: FunctionComplexity) -> str:
    """What :func:`judged_ccn` did not count, for a finding's text."""
    unguarded = fn.ccn - fn.exit_guards - fn.copy_guards
    if fn.dispatch_share >= DISPATCH_SHARE and judged_ccn(fn) < unguarded:
        return "every arm of its dispatch"
    if fn.copy_guards and fn.exit_guards:
        return "every repeated error guard and guarded copy"
    if fn.copy_guards:
        return "every repeated guarded copy"
    return "every repeated error guard"


def judged_nesting(fn: FunctionComplexity) -> int:
    """The nesting a marker judges *fn* by: less the dispatch's own levels once it dominates.

    The deepest block of a dominated function is almost always in an arm, so
    this subtracts :data:`_DISPATCH_LEVELS`. A block that deep outside the
    dispatch is under-read by the same amount (the ceiling of not tracking
    which side the deepest block is on).
    """
    if fn.dispatch_share < DISPATCH_SHARE:
        return fn.max_nesting
    return max(fn.max_nesting - _DISPATCH_LEVELS, 0)
