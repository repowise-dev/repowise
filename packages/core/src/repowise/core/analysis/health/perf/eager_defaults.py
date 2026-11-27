"""Values a function builds as an eager default: built on every call, used only on a miss.

``d.setdefault(key, Tracker())`` evaluates ``Tracker()`` before it looks the key
up, so it builds one on every call, and throws it away whenever the key is
already there. Read off the syntax of one Python function on the complexity
walk; the next step (``evidence.next_step``) names it only when a profile shows
that build holding the function's time.

A default is recorded only when it is the function's one call to that name:
a profile frame names the callee, not the call site, so a second call to the
same name (``Tracker()`` beside the default, or ``copy.deepcopy`` beside a
``self.deepcopy()`` default) leaves the time unattributed and the function
without a fact.

Python's ``setdefault`` by name only. Ceiling: a receiver whose own
``setdefault`` reads its default on a hit (a ``dict`` subclass override) reads
the same; upgrade path: the receiver's type once the walk resolves it. Left out:
``d.get(key, Tracker())`` and ``d.pop``, whose receiver may not be a mapping
(``requests.get(url, params())``), and ``getattr(obj, name, Tracker())``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

#: One eager default: the built call's callee as written, and its 1-indexed line.
EagerDefault = tuple[str, int]

# A callee longer than this is not a name a reader looks for; it is left out.
_MAX_CALLEE = 120
_SKIP_ARGS = frozenset({"comment", "keyword_argument", "list_splat", "dictionary_splat"})
# Code that does not run on the function's own calls: a lambda's or a nested
# class body's runs elsewhere, a generator expression's later, and a parameter
# default or decorator once, when the function is defined.
_OTHER_SCOPE = frozenset(
    {"lambda", "class_definition", "generator_expression", "parameters", "decorator"}
)


def eager_defaults(
    root: Node, source: bytes, functions: Mapping[int, object]
) -> dict[int, tuple[EagerDefault, ...]]:
    """``{function node id: eager defaults}`` for a Python tree.

    *functions* holds the node ids of the walk's function nodes; each call
    belongs to the innermost one whose own calls run it.
    """
    if b"setdefault" not in source:
        return {}
    found: dict[int, list[EagerDefault]] = {}
    calls: dict[tuple[int, str], int] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "call":
            continue
        name = _callee(node)
        if name is not None:
            # Counted in every function around the call, nested definitions'
            # parameter defaults and decorators included: a count too high
            # only drops a fact.
            for around in _functions_around(node, functions):
                key = (around, _last(name))
                calls[key] = calls.get(key, 0) + 1
        eager = _eager_default(node)
        owner = _owner(node, functions) if eager is not None else None
        if eager is not None and owner is not None:
            found.setdefault(owner, []).append(eager)
    out: dict[int, tuple[EagerDefault, ...]] = {}
    for owner, defaults in found.items():
        kept = sorted(
            (d for d in defaults if calls.get((owner, _last(d[0]))) == 1), key=lambda d: d[1]
        )
        if kept:
            out[owner] = tuple(kept)
    return out


def _callee(call: Node) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.text is None:
        return None
    name = fn.text.decode("utf-8", "replace")
    return None if len(name) > _MAX_CALLEE or "\n" in name else name


def _last(name: str) -> str:
    return name.replace("(", "").replace(")", "").rsplit(".", 1)[-1].strip()


def _functions_around(node: Node, functions: Mapping[int, object]) -> list[int]:
    out: list[int] = []
    parent = node.parent
    while parent is not None:
        if parent.id in functions:
            out.append(parent.id)
        parent = parent.parent
    return out


def _eager_default(call: Node) -> EagerDefault | None:
    """``(callee, line)`` when *call* is ``<x>.setdefault(<key>, <callee>(...))``."""
    built = _unwrapped(_setdefault_default(call))
    if built is None or built.type != "call":
        return None
    name = _callee(built)
    return (name, built.start_point[0] + 1) if name is not None else None


def _setdefault_default(call: Node) -> Node | None:
    """The default argument of a two-argument ``<x>.setdefault(key, default)`` call."""
    fn = call.child_by_field_name("function")
    attr = (
        fn.child_by_field_name("attribute") if fn is not None and fn.type == "attribute" else None
    )
    if attr is None or attr.text != b"setdefault":
        return None
    args = call.child_by_field_name("arguments")
    if args is None or args.type != "argument_list":
        return None
    named = [a for a in args.named_children if a.type != "comment"]
    if len(named) != 2 or any(a.type in _SKIP_ARGS for a in named):
        return None
    return named[1]


def _unwrapped(node: Node | None) -> Node | None:
    """*node* without grouping parentheses (comments inside them ignored)."""
    while node is not None and node.type == "parenthesized_expression":
        inner = [c for c in node.named_children if c.type != "comment"]
        node = inner[0] if len(inner) == 1 else None
    return node


def _owner(node: Node, functions: Mapping[int, object]) -> int | None:
    parent = node.parent
    while parent is not None:
        if parent.id in functions:
            return parent.id
        if parent.type in _OTHER_SCOPE:
            return None
        parent = parent.parent
    return None


__all__ = ["EagerDefault", "eager_defaults"]
