"""The TS/JS anti-pattern marker hooks: list membership, deep-clone, spread-in-reduce,
and client construction inside a loop."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# Heavy client/connection classes that should be hoisted, not re-``new``-ed each
# iteration. Distinctive names only (``Client`` / ``Pool`` collide with worker
# pools and unrelated SDKs, so they are deliberately excluded for precision).
_TS_RESOURCE_CTORS: frozenset[str] = frozenset(
    {
        "PrismaClient",
        "MongoClient",
        "Sequelize",
        "DataSource",
        "Redis",
        "IORedis",
    }
)

_FN_LITERAL_KINDS: frozenset[str] = frozenset({"arrow_function", "function", "function_expression"})


# -- TS/JS syntax helpers (shared with ts_js.py) -------------------------------


def node_text(node: Node | None) -> str | None:
    """*node*'s source text, or None when there is no node or no text."""
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8", "replace")


def identifier_name(node: Node | None) -> str | None:
    """The name *node* spells when it is a bare identifier."""
    return node_text(node) if node is not None and node.type == "identifier" else None


def first_named_child(node: Node) -> Node | None:
    return next((c for c in node.children if c.is_named), None)


# -- marker predicates ---------------------------------------------------------


def _param_names(fn: Node) -> Iterator[str]:
    """Names of an arrow/function's identifier parameters, in order.

    A destructured parameter binds no single name and is skipped.
    """
    params = fn.child_by_field_name("parameters")
    if params is None:
        # ``x => …`` single unparenthesized param.
        candidates = [first_named_child(fn)]
    else:
        candidates = [
            c if c.type == "identifier" else c.child_by_field_name("pattern")
            for c in params.children
        ]
    for candidate in candidates:
        name = identifier_name(candidate)
        if name is not None:
            yield name


def _is_json_deep_clone(call: Node) -> bool:
    """True if the call's first argument is itself a ``JSON.stringify(...)``
    call — the ``JSON.parse(JSON.stringify(x))`` deep-clone idiom."""
    args = call.child_by_field_name("arguments")
    first = first_named_child(args) if args is not None else None
    if first is None or first.type != "call_expression":
        return False
    return node_text(first.child_by_field_name("function")) == "JSON.stringify"


def _reduce_spreads_accumulator(call: Node) -> bool:
    """A ``reduce`` whose callback spreads its OWN accumulator param into a fresh
    array / object literal."""
    args = call.child_by_field_name("arguments")
    cb = first_named_child(args) if args is not None else None
    if cb is None or cb.type not in _FN_LITERAL_KINDS:
        return False
    acc = next(_param_names(cb), None)
    body = cb.child_by_field_name("body")
    return acc is not None and body is not None and _spreads_name_in_collection(body, acc)


def _spreads_name_in_collection(body: Node, name: str) -> bool:
    """True if *body* spreads ``name`` into an array / object literal
    (``[...name, x]`` / ``{...name}``) — the O(n^2) accumulator rebuild.

    Stops at a nested arrow/function that re-binds ``name`` as its own
    parameter: a spread of ``name`` inside such a scope targets THAT binding
    (e.g. an inner ``reduce`` with its own ``acc``), not the outer
    accumulator, so it must not be attributed to the outer reduce.
    """
    stack: list[Node] = [body]
    while stack:
        n = stack.pop()
        if n != body and n.type in _FN_LITERAL_KINDS and name in _param_names(n):
            continue
        if _is_collection_spread_of(n, name):
            return True
        stack.extend(n.children)
    return False


def _is_collection_spread_of(node: Node, name: str) -> bool:
    """``[...name]`` / ``{...name}``."""
    parent = node.parent
    if node.type != "spread_element" or parent is None or parent.type not in ("array", "object"):
        return False
    return identifier_name(first_named_child(node)) == name


class TsJsMarkerHooks(BasePerfDialect):
    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``arr.includes(x)`` where ``arr`` is a known array -> O(n) membership.
        if method == "includes" and root in list_names:
            return "membership_test_against_list_in_loop"
        # ``JSON.parse(JSON.stringify(x))`` deep-clone in a loop is the canonical
        # waste (use ``structuredClone``). Gate: a BARE ``JSON.parse`` /
        # ``JSON.stringify`` per iteration was 0% precision (30/30 were
        # format-conversion loops serializing a DISTINCT payload each pass —
        # necessary work, not waste), so the marker is restricted to the
        # deep-clone idiom, which is unconditionally hoistable.
        if root == "JSON" and method == "parse" and _is_json_deep_clone(node):
            return "json_parse_in_loop"
        return None

    def bare_call_marker(self, root: str, method: str, node: Node) -> str | None:
        # ``arr.reduce((acc, x) => [...acc, x], [])`` rebuilds the accumulator
        # every step -> O(n^2). The ``.reduce`` IS the loop, so this fires at any
        # depth. Precision-first: only when the callback spreads its OWN
        # accumulator param into a fresh array / object literal.
        if method == "reduce" and _reduce_spreads_accumulator(node):
            return "array_spread_in_reduce"
        return None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``new PrismaClient(...)`` etc. is a ``new_expression`` (not a
        # ``call_expression``), so it arrives here rather than via the call path.
        if node.type != "new_expression":
            return None
        # Rightmost identifier of a ``new X()`` / ``new pkg.X()`` constructor.
        ctor = node_text(node.child_by_field_name("constructor"))
        if ctor is not None and ctor.split(".")[-1] in _TS_RESOURCE_CTORS:
            return "resource_construction_in_loop"
        return None

    def list_bound_names(self, root: Node) -> frozenset[str]:
        """Names bound to an array literal (``const arr = [...]``)."""
        names: set[str] = set()
        for n in self._walk(root):
            if n.type != "variable_declarator":
                continue
            name = identifier_name(n.child_by_field_name("name"))
            value = n.child_by_field_name("value")
            if name is not None and value is not None and value.type == "array":
                names.add(name)
        return frozenset(names)
