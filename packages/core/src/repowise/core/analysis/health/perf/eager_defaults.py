"""Values a function builds as an eager default: built on every call, used only on a miss.

``d.setdefault(key, Tracker())`` evaluates ``Tracker()`` before it looks the key
up, so it builds one on every call, and throws it away whenever the key is
already there. The fix is to build only when the key is missing, which is the
same program for any default: ``setdefault`` stores and returns the default
only on a miss. Read off the syntax of one Python function on the complexity
walk; the next step (``evidence.next_step``) names it only when a profile shows
that build holding the function's time.

Python's ``dict.setdefault`` only: the name is unambiguous and its default is a
positional argument. Left out: ``d.get(key, Tracker())`` and ``d.pop``, whose
receiver may not be a mapping (``requests.get(url, params())``), and
``getattr(obj, name, Tracker())`` (ceiling; upgrade path: read them once the
walk knows the receiver is a mapping).
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


def eager_defaults(
    root: Node, source: bytes, functions: Mapping[int, object]
) -> dict[int, tuple[EagerDefault, ...]]:
    """``{function node id: eager defaults}`` for a Python tree.

    *functions* holds the node ids of the walk's function nodes; each default
    belongs to the innermost one around it. A default inside a lambda or a
    class body nested in the function is left out: it does not run on the
    function's own calls.
    """
    if b"setdefault" not in source:
        return {}
    out: dict[int, list[EagerDefault]] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "call":
            found = _eager_default(node)
            owner = _owner(node, functions) if found else None
            if found and owner is not None:
                out.setdefault(owner, []).append(found)
        stack.extend(node.children)
    return {owner: tuple(sorted(found, key=lambda d: d[1])) for owner, found in out.items()}


def _eager_default(call: Node) -> EagerDefault | None:
    """``(callee, line)`` when *call* is ``<x>.setdefault(<key>, <callee>(...))``."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return None
    attr = fn.child_by_field_name("attribute")
    if attr is None or attr.text != b"setdefault":
        return None
    args = call.child_by_field_name("arguments")
    if args is None or args.type != "argument_list":
        return None
    named = [a for a in args.named_children if a.type != "comment"]
    if len(named) != 2 or any(a.type in _SKIP_ARGS for a in named):
        return None
    built = named[1]
    if built.type != "call":
        return None
    callee = built.child_by_field_name("function")
    if callee is None or callee.text is None:
        return None
    name = callee.text.decode("utf-8", "replace")
    if len(name) > _MAX_CALLEE or "\n" in name:
        return None
    return name, built.start_point[0] + 1


def _owner(node: Node, functions: Mapping[int, object]) -> int | None:
    parent = node.parent
    while parent is not None:
        if parent.id in functions:
            return parent.id
        if parent.type in ("lambda", "class_definition"):
            return None
        parent = parent.parent
    return None


__all__ = ["EagerDefault", "eager_defaults"]
