"""The TS/JS anti-pattern marker hooks: list membership, deep-clone, spread-in-reduce,
and client construction inside a loop."""

from __future__ import annotations

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
        if root == "JSON" and method == "parse" and self._arg_is_json_stringify(node):
            return "json_parse_in_loop"
        return None

    @staticmethod
    def _arg_is_json_stringify(node: Node) -> bool:
        """True if the call's first argument is itself a ``JSON.stringify(...)``
        call — the ``JSON.parse(JSON.stringify(x))`` deep-clone idiom."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        first = next((c for c in args.children if c.is_named), None)
        if first is None or first.type != "call_expression":
            return False
        fn = first.child_by_field_name("function")
        return (
            fn is not None
            and fn.text is not None
            and fn.text.decode("utf-8", "replace") == "JSON.stringify"
        )

    def bare_call_marker(self, root: str, method: str, node: Node) -> str | None:
        # ``arr.reduce((acc, x) => [...acc, x], [])`` rebuilds the accumulator
        # every step -> O(n^2). The ``.reduce`` IS the loop, so this fires at any
        # depth. Precision-first: only when the callback spreads its OWN
        # accumulator param into a fresh array / object literal.
        if method != "reduce":
            return None
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        cb = next((c for c in args.children if c.is_named), None)
        if cb is None or cb.type not in ("arrow_function", "function", "function_expression"):
            return None
        acc = self._first_param_name(cb)
        body = cb.child_by_field_name("body")
        if acc is None or body is None:
            return None
        return "array_spread_in_reduce" if self._spreads_name_in_collection(body, acc) else None

    @staticmethod
    def _first_param_name(cb: Node) -> str | None:
        """First parameter identifier of an arrow/function (the reduce accumulator)."""
        params = cb.child_by_field_name("parameters")
        if params is not None:
            for c in params.children:
                ident = c if c.type == "identifier" else c.child_by_field_name("pattern")
                if ident is not None and ident.type == "identifier" and ident.text is not None:
                    return ident.text.decode("utf-8", "replace")
            return None
        # ``x => …`` single unparenthesized param.
        first = next((c for c in cb.children if c.is_named), None)
        if first is not None and first.type == "identifier" and first.text is not None:
            return first.text.decode("utf-8", "replace")
        return None

    @staticmethod
    def _params_contain(fn: Node, name: str) -> bool:
        """True if any parameter of ``fn`` binds ``name`` (a shadow of an outer
        accumulator). Handles both parenthesized parameter lists and the single
        unparenthesized ``x => …`` form."""
        params = fn.child_by_field_name("parameters")
        if params is not None:
            for c in params.children:
                ident = c if c.type == "identifier" else c.child_by_field_name("pattern")
                if (
                    ident is not None
                    and ident.type == "identifier"
                    and ident.text is not None
                    and ident.text.decode("utf-8", "replace") == name
                ):
                    return True
            return False
        first = next((c for c in fn.children if c.is_named), None)
        return (
            first is not None
            and first.type == "identifier"
            and first.text is not None
            and first.text.decode("utf-8", "replace") == name
        )

    _FN_LITERAL_KINDS: frozenset[str] = frozenset(
        {"arrow_function", "function", "function_expression"}
    )

    @staticmethod
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
            if (
                n != body
                and n.type in TsJsMarkerHooks._FN_LITERAL_KINDS
                and TsJsMarkerHooks._params_contain(n, name)
            ):
                continue
            if (
                n.type == "spread_element"
                and n.parent is not None
                and n.parent.type
                in (
                    "array",
                    "object",
                )
            ):
                arg = next((c for c in n.children if c.is_named), None)
                if (
                    arg is not None
                    and arg.type == "identifier"
                    and arg.text is not None
                    and arg.text.decode("utf-8", "replace") == name
                ):
                    return True
            stack.extend(n.children)
        return False

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``new PrismaClient(...)`` etc. is a ``new_expression`` (not a
        # ``call_expression``), so it arrives here rather than via the call path.
        if node.type != "new_expression":
            return None
        ctor = self._constructor_name(node)
        return "resource_construction_in_loop" if ctor in _TS_RESOURCE_CTORS else None

    @staticmethod
    def _constructor_name(node: Node) -> str | None:
        """Rightmost identifier of a ``new X()`` / ``new pkg.X()`` constructor."""
        ctor = node.child_by_field_name("constructor")
        if ctor is None or ctor.text is None:
            return None
        return ctor.text.decode("utf-8", "replace").split(".")[-1]

    def list_bound_names(self, root: Node) -> frozenset[str]:
        """Names bound to an array literal (``const arr = [...]``)."""
        names: set[str] = set()
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if n.type == "variable_declarator":
                name = n.child_by_field_name("name")
                value = n.child_by_field_name("value")
                if (
                    name is not None
                    and name.type == "identifier"
                    and name.text is not None
                    and value is not None
                    and value.type == "array"
                ):
                    names.add(name.text.decode("utf-8", "replace"))
            for c in n.children:
                stack.append(c)
        return frozenset(names)
