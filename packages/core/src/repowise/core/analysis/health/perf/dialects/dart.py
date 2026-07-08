"""Dart perf dialect.

Dart has no call-expression node: a call is a ``selector`` node carrying an
``argument_part``, chained flat after the receiver (``client.get(u)`` parses
as ``identifier selector(.get) selector((u))``). ``call_kinds`` therefore
routes every ``selector`` here, and the callee extraction returns ``None``
for selectors without an ``argument_part`` so plain member accesses produce
no signal.

Markers (precision-first, matching the walker's shared shapes):

- ``io_in_loop`` / ``serial_await_in_loop`` — http/dio verbs, ``File``
  ``readAs*``/``writeAs*``, sqflite ``raw*`` (+ ``query``/``execute`` gated
  on a db import), ``Process.run``. Dart is async-heavy, so the awaited
  serial round-trip is the highest-value marker.
- ``string_concat_in_loop`` — Dart strings are immutable; ``s += ...`` in a
  loop is a real O(n^2) accumulation (StringBuffer is the fix). A target
  declared fresh inside the loop body is excluded (shelf FP, live-reviewed).
- ``resource_construction_in_loop`` — ``HttpClient()`` / ``Dio()`` /
  ``http.Client()`` built per-iteration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

_HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "read", "readBytes"}
)
# package:dio adds its own verbs on top of the shared HTTP set.
_DIO_EXTRA: frozenset[str] = frozenset({"request", "download", "fetch"})
# dart:io File round-trips — method names unique enough to fire without a
# receiver type (nothing but a file handle has readAsString).
_FILE_METHODS: frozenset[str] = frozenset(
    {
        "readAsString",
        "readAsStringSync",
        "readAsBytes",
        "readAsBytesSync",
        "readAsLines",
        "readAsLinesSync",
        "writeAsString",
        "writeAsStringSync",
        "writeAsBytes",
        "writeAsBytesSync",
        "openRead",
        "openWrite",
    }
)
# sqflite raw statements — unambiguous db round-trips.
_DB_RAW_METHODS: frozenset[str] = frozenset({"rawQuery", "rawInsert", "rawUpdate", "rawDelete"})
# Generic executor verbs — only db when the file imports a db package.
_DB_AMBIGUOUS_METHODS: frozenset[str] = frozenset({"query", "execute"})
_SUBPROC_METHODS: frozenset[str] = frozenset({"run", "runSync", "start"})
# Per-iteration heavy-client constructions.
_RESOURCE_CTORS: frozenset[str] = frozenset({"HttpClient", "Dio"})

_LITERAL_KINDS: frozenset[str] = frozenset(
    {
        "decimal_integer_literal",
        "decimal_floating_point_literal",
        "string_literal",
        "true",
        "false",
    }
)


class DartPerfDialect(BasePerfDialect):
    language = "dart"
    markers = frozenset(
        {
            "io_in_loop",
            # Record own data-dependent loops so a caller looping over this
            # function becomes a cross-function quadratic target.
            "interprocedural_quadratic_loop",
            "serial_await_in_loop",
            "string_concat_in_loop",
            "resource_construction_in_loop",
        }
    )
    string_literal_kinds = frozenset({"string_literal"})
    aug_assign_kinds = frozenset({"assignment_expression"})

    # -- callee extraction over selector chains -------------------------------

    @staticmethod
    def _chain(call_node: Node) -> tuple[list[Node], int]:
        """Named siblings of the selector chain + this node's index in it."""
        parent = call_node.parent
        if parent is None:
            return [], -1
        sibs = [c for c in parent.children if c.is_named]
        for i, sib in enumerate(sibs):
            if sib == call_node:
                return sibs, i
        return [], -1

    @staticmethod
    def _selector_member(node: Node) -> str | None:
        """``.name`` selector -> 'name', else None."""
        if node.type != "selector":
            return None
        for child in node.children:
            if child.type in (
                "unconditional_assignable_selector",
                "conditional_assignable_selector",
            ):
                for sub in child.children:
                    if sub.type == "identifier" and sub.text:
                        return sub.text.decode("utf-8", "replace")
        return None

    @staticmethod
    def _is_call_selector(node: Node) -> bool:
        return node.type == "selector" and any(c.type == "argument_part" for c in node.children)

    def callee_method_name(self, call_node: Node) -> str | None:
        if not self._is_call_selector(call_node):
            return None
        sibs, idx = self._chain(call_node)
        if idx <= 0:
            return None
        prev = sibs[idx - 1]
        member = self._selector_member(prev)
        if member is not None:
            return member
        if prev.type == "identifier" and prev.text:
            # Bare call: ``foo(args)`` / constructor ``HttpClient()``.
            return prev.text.decode("utf-8", "replace")
        return None

    def callee_root_name(self, call_node: Node) -> str | None:
        if not self._is_call_selector(call_node):
            return None
        sibs, idx = self._chain(call_node)
        if idx <= 0:
            return None
        head = sibs[0]
        if head.type == "identifier" and head.text:
            return head.text.decode("utf-8", "replace")
        if head.type == "this":
            return "this"
        return None

    def callee_is_attribute(self, call_node: Node) -> bool:
        sibs, idx = self._chain(call_node)
        if idx <= 0:
            return False
        return self._selector_member(sibs[idx - 1]) is not None

    # -- sink classification ---------------------------------------------------

    def sink_kind(
        self,
        root: str,
        method: str,
        *,
        awaited: bool,
        is_attribute: bool,
        io_names: dict[str, str],
        has_db_import: bool,
    ) -> str | None:
        if not method:
            return None
        if method in _FILE_METHODS:
            return "filesystem"
        if method in _DB_RAW_METHODS:
            return "db"
        if root == "Process" and method in _SUBPROC_METHODS:
            return "subprocess"
        if is_attribute and method in (_HTTP_VERBS | _DIO_EXTRA):
            root_kind = io_names.get(root)
            if root_kind == "network" or root in ("http", "dio"):
                return "network"
        if is_attribute and has_db_import and method in _DB_AMBIGUOUS_METHODS:
            return "db"
        return None

    # -- loop predicates -------------------------------------------------------

    def is_constant_loop(self, node: Node) -> bool:
        """``for (var i = 0; i < 3; i++)`` and ``for (x in [1, 2])`` are not
        data-dependent — mirror the Python range-literal skip."""
        if node.type != "for_statement":
            return False
        parts = next((c for c in node.children if c.type == "for_loop_parts"), None)
        if parts is None:
            return False
        value = parts.child_by_field_name("value")
        if value is not None:
            return value.type in ("list_literal", "set_or_map_literal") and all(
                (not c.is_named) or c.type in _LITERAL_KINDS for c in value.children
            )
        rel = next((c for c in parts.children if c.type == "relational_expression"), None)
        if rel is not None:
            return any(c.type == "decimal_integer_literal" for c in rel.children)
        return False

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type != "for_statement":
            return None
        parts = next((c for c in node.children if c.type == "for_loop_parts"), None)
        if parts is None:
            return None
        return self._dotted_path(parts.child_by_field_name("value"))

    # -- extra markers ---------------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        if method in _RESOURCE_CTORS and not self.callee_is_attribute(node):
            return "resource_construction_in_loop"
        if root == "http" and method == "Client":
            return "resource_construction_in_loop"
        return None

    def is_string_concat(self, node: Node) -> bool:
        """Base check plus a reset gate: ``var name = ...; name += '/'``
        inside one iteration is not a cross-iteration accumulator (mirrors
        the Python dialect's ``_resets_name``)."""
        if not super().is_string_concat(node):
            return False
        target = next((c for c in node.children if c.is_named), None)
        if target is None or target.text is None:
            return True
        name = target.text.decode("utf-8", "replace")
        loop = node.parent
        for _ in range(32):
            if loop is None:
                return True
            if loop.type in ("for_statement", "while_statement", "do_statement"):
                break
            loop = loop.parent
        body = loop.child_by_field_name("body")
        if body is None:
            return True
        stack = list(body.children)
        while stack:
            cur = stack.pop()
            if cur.type == "local_variable_declaration":
                decl_id = next(
                    (
                        d
                        for c in cur.children
                        if c.type == "initialized_variable_definition"
                        for d in c.children
                        if d.type == "identifier"
                    ),
                    None,
                )
                if (
                    decl_id is not None
                    and decl_id.text is not None
                    and decl_id.text.decode("utf-8", "replace") == name
                ):
                    return False  # declared fresh every iteration
            stack.extend(cur.children)
        return True

    def _rhs_is_stringish(self, node: Node) -> bool:
        """Dart's ``assignment_expression`` exposes no ``right`` field — the
        RHS is the last named child (possibly an ``additive_expression``
        concatenation)."""
        named = [c for c in node.children if c.is_named]
        if not named:
            return False
        right = named[-1]
        if right.type in self.string_literal_kinds:
            return True
        if right.type == "additive_expression":
            return any(c.type in self.string_literal_kinds for c in right.children)
        return False


DIALECT = DartPerfDialect()
