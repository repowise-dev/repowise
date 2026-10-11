"""What an Extract Method span is about, read off its code, for the helper's name.

The last naming source, after a stage label, a banner and ``compute_<out>``
(:mod:`.helper_naming`): the host function's name qualified by the span's
subject, ``render_card`` + ``directive`` -> ``render_card_directive``. Both
halves are facts the plan holds, so the name says where the helper came from
and what it handles without guessing a verb for what it does.

The subject, first found: the span's single OUT value; the condition of the
``if`` arm or the target of the loop whose whole body the span is; then for
each of the span's statements in order, an ``if`` condition's subject (a string
key or compared literal, else the last name read), a loop's iterable, the
first name a statement binds, or a dropped call's name. Role words (``result``,
``self``) and one-letter names say nothing and are skipped.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..dataflow.effects import binding_target, bound_names
from ..dataflow.span import body_holder, is_comment
from .naming import split_words

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap

# Names that describe a role or a receiver, not what a block handles.
_ROLE_WORDS = frozenset(
    {"out", "result", "results", "value", "values", "ret", "tmp", "temp", "data", "item"}
    | {"self", "this", "cls", "none", "null", "undefined", "true", "false", "args", "kwargs"}
    # ``typeof x === "number"`` compares a type name, not the subject.
    | {"number", "string", "boolean", "object", "function", "bigint", "symbol"}
)
# A dropped call to one of these adds to something; the call's name says
# nothing about what (``out.append(...)``), so the next statement is read.
_GENERIC_CALLS = frozenset(
    {"append", "push", "add", "extend", "insert", "update", "set", "write", "print", "echo"}
    | {"log", "info", "debug", "warning", "warn", "error", "emit"}
)
# Calls that pass their subject through: ``card.get("owner")`` is about
# ``owner``, ``enumerate(groups)`` about ``groups``. ``_GENERIC_CALLS`` do too.
_PASS_THROUGH = frozenset(
    {"get", "items", "keys", "values", "enumerate", "sorted", "reversed", "list", "tuple"}
    | {"len", "iter", "getattr", "isinstance", "bool", "str", "int", "object", "array"}
)
_MEMBER_FIELDS = ("attribute", "property", "field")
_CALLEE_FIELDS = ("function", "callee")
_LOOP_SOURCE_FIELDS = ("right", "value", "iterable", "condition")
_PLAIN_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_MAX_DEPTH = 8


def host_words(name: str) -> list[str]:
    """The host function's name as words; ``[]`` for an anonymous or dotted one."""
    return split_words(name) if _PLAIN_NAME.fullmatch(name or "") else []


def subjects(stmts: list[Any], returns: tuple[str, ...], lmap: LanguageNodeMap) -> list[list[str]]:
    """The span's subjects, best first, each as words, without repeats."""
    found: list[list[str]] = []
    if len(returns) == 1:
        found.append(_words(returns[0]))
    found.append(_enclosing_subject(stmts, lmap))
    code = [s for s in stmts if not is_comment(s)]
    for stmt in code:
        found.append(_statement_subject(stmt, lmap))
        if stmt.type in lmap.loop_kinds:
            # Over the host's own subject, the element is what the span handles.
            found.append(_expr_words(stmt.child_by_field_name("left"), 0))
    out: list[list[str]] = []
    for words in found:
        if words and words not in out:
            out.append(words)
    return out


def _words(text: str) -> list[str]:
    words = split_words(text)
    if not words or "_".join(words) in _ROLE_WORDS or all(len(w) == 1 for w in words):
        return []
    return words


def _enclosing_subject(stmts: list[Any], lmap: LanguageNodeMap) -> list[str]:
    """The subject of the ``if`` arm or loop whose whole body *stmts* is."""
    holder = body_holder(stmts)
    if holder is None:
        return []
    if holder.type in lmap.loop_kinds:
        return _expr_words(holder.child_by_field_name("left"), 0)
    return _expr_words(holder.child_by_field_name("condition"), 0)


def _statement_subject(stmt: Any, lmap: LanguageNodeMap) -> list[str]:
    kind = stmt.type
    if kind in lmap.if_kinds:
        return _expr_words(stmt.child_by_field_name("condition"), 0)
    if kind in lmap.loop_kinds:
        return _expr_words(_child(stmt, _LOOP_SOURCE_FIELDS), 0)
    if kind in lmap.try_kinds or kind in lmap.with_kinds:
        return _wrapper_subject(stmt, lmap)
    expr = stmt.named_children[0] if stmt.named_child_count == 1 else stmt
    if expr.type in lmap.await_kinds | {"await_expression"} and expr.named_child_count == 1:
        expr = expr.named_children[0]
    bound = _bound_subject(expr, lmap)
    if bound is not None:
        return bound
    if expr.type not in lmap.call_kinds or _generic(_callee_words(expr, 0)):
        # ``out.append(...)`` says nothing about what is added: read on.
        return []
    return _expr_words(expr, 0)


def _wrapper_subject(stmt: Any, lmap: LanguageNodeMap) -> list[str]:
    """A ``try`` or ``with`` is about what its body opens with."""
    body = stmt.child_by_field_name("body") or next(
        (c for c in stmt.named_children if c.type in lmap.block_kinds), None
    )
    first = next((c for c in body.named_children if not is_comment(c)), None) if body else None
    return _statement_subject(first, lmap) if first is not None else []


def _bound_subject(expr: Any, lmap: LanguageNodeMap) -> list[str] | None:
    """``x = ...`` is about ``x``, ``state.registry = ...`` about ``registry``;
    ``None`` when *expr* binds nothing."""
    holders = [expr, *expr.named_children]
    target = next((t for h in holders if (t := binding_target(h, lmap)) is not None), None)
    if target is None:
        return None
    names = bound_names(target)
    return _words(names[0]) if names else _expr_words(target, 0)


def _expr_words(node: Any, depth: int) -> list[str]:
    """The name an expression is about (module docstring)."""
    if node is None or depth > _MAX_DEPTH:
        return []
    if "string" in node.type:
        return _literal_words(node)
    if not node.named_children:
        text = (node.text or b"").decode("utf-8", "replace")
        return _words(text) if _PLAIN_NAME.fullmatch(text) else []
    if node.type in ("call", "call_expression"):
        return _call_words(node, depth)
    member = _child(node, _MEMBER_FIELDS)
    # A member names its field; otherwise a compared key, then the first operand.
    keys = [c for c in node.named_children if "string" in c.type]
    parts = [member] if member is not None else [*keys, *node.named_children]
    return next((w for c in parts if (w := _expr_words(c, depth + 1))), [])


def _callee_words(call: Any, depth: int) -> list[str]:
    return _expr_words(_child(call, _CALLEE_FIELDS), depth + 1)


def _generic(words: list[str]) -> bool:
    return "_".join(words) in _GENERIC_CALLS


def _call_words(call: Any, depth: int) -> list[str]:
    """A call is about its name, or, when the name only passes a value on
    (``get``, ``len``, ``append``), its first argument, then its receiver."""
    name = _callee_words(call, depth)
    if name and not _generic(name) and "_".join(name) not in _PASS_THROUGH:
        return name
    callee = _child(call, _CALLEE_FIELDS)
    args = call.child_by_field_name("arguments")
    first = next((a for a in args.named_children if not is_comment(a)), None) if args else None
    receiver = _child(callee, ("object", "value")) if callee is not None else None
    return next((w for c in (first, receiver) if (w := _expr_words(c, depth + 1))), [])


def _child(node: Any, fields: tuple[str, ...]) -> Any:
    return next((c for f in fields if (c := node.child_by_field_name(f)) is not None), None)


def _literal_words(literal: Any) -> list[str]:
    """A string literal that is a plain key (``"recent_owner"``), as words."""
    text = (literal.text or b"").decode("utf-8", "replace").strip("'\"`")
    return _words(text) if _PLAIN_NAME.fullmatch(text) else []


__all__ = ["host_words", "subjects"]
