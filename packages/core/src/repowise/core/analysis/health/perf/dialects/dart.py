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

import re
from typing import TYPE_CHECKING

from .. import loop_lexicon as lex
from .iterables import IterableSources, text_of

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import SinkProbe
    from .iterables import Source

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


_DART_TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_DART_RECURSIVE_ARG = re.compile(rb"\brecursive\s*:\s*true\b")
_DART_NON_LITERAL_ENTRIES = frozenset({"spread_element", "if_element", "for_element"})


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


def _is_call_selector(node: Node) -> bool:
    return node.type == "selector" and any(c.type == "argument_part" for c in node.children)


def _unwrap_once(cur: Node) -> Node | None:
    if cur.type == "unary_expression":
        return next((c for c in cur.named_children if c.type == "await_expression"), None)
    if cur.type in ("await_expression", "parenthesized_expression", "argument"):
        return next((c for c in reversed(cur.named_children) if c.type != "comment"), None)
    return None


def _chain_node_list(nodes: list[Node]) -> Node | None:
    """Trailing expression node from a Dart chain, unwrapping parens and await."""
    cur: Node | None = nodes[-1] if nodes else None
    while cur is not None:
        nxt = _unwrap_once(cur)
        if nxt is None:
            break
        cur = nxt
    return cur


def _counted_bound(rel: Node) -> Node | None:
    for i, child in enumerate(rel.children):
        if child.type == "relational_operator" and child.text in (b"<", b"<="):
            rhs = [c for c in rel.children[i + 1 :] if c.is_named and c.type != "comment"]
            return _chain_node_list(rhs)
    return None


def _normalize_arg(arg: Node) -> Node:
    if arg.type != "argument":
        return arg
    return _chain_node_list([arg]) or arg


def _call_args(call_selector: Node) -> list[Node]:
    arg_part = next((c for c in call_selector.children if c.type == "argument_part"), None)
    args_node = (
        next((c for c in arg_part.children if c.type == "arguments"), None)
        if arg_part is not None
        else None
    )
    if args_node is None:
        return []
    return [_normalize_arg(c) for c in args_node.named_children if "comment" not in c.type]


def _is_type_name(node: Node | None) -> bool:
    return (
        node is not None
        and node.type == "identifier"
        and bool(_DART_TYPE_NAME.match(text_of(node)))
    )


def _var_binding(node: Node) -> list[tuple[bytes, Node]]:
    name = node.child_by_field_name("name")
    val = _chain_node_list(node.children_by_field_name("value"))
    return [(name.text, val)] if name is not None and name.text and val is not None else []


def _assign_binding(node: Node) -> list[tuple[bytes, Node]]:
    left = node.child_by_field_name("left")
    op = node.child_by_field_name("operator")
    if left is None or op is None or op.type != "=" or left.type != "assignable_expression":
        return []
    if [c.type for c in left.named_children] != ["identifier"]:
        return []
    val = _chain_node_list(node.children_by_field_name("right"))
    return [(left.text, val)] if left.text and val is not None else []


def _for_loop_header(loop: Node) -> tuple[str, Node | None]:
    parts = next((c for c in loop.children if c.type == "for_loop_parts"), None)
    if parts is None:
        return "each", None
    values = parts.children_by_field_name("value")
    if values:
        return "each", _chain_node_list(values)
    cond = parts.child_by_field_name("condition")
    if cond is not None and cond.type == "relational_expression":
        return "counted", _counted_bound(cond)
    return "each", None


def _rhs_is_stringish(node: Node, string_literal_kinds: frozenset[str]) -> bool:
    """Dart's ``assignment_expression`` exposes no ``right`` field — the
    RHS is the last named child (possibly an ``additive_expression``
    concatenation)."""
    named = [c for c in node.children if c.is_named]
    if not named:
        return False
    right = named[-1]
    if right.type in string_literal_kinds:
        return True
    if right.type == "additive_expression":
        return any(c.type in string_literal_kinds for c in right.children)
    return False


class DartLoopSources(IterableSources):
    """Dart ``IterableSources`` configuration and AST binding/call extractors."""

    function_kinds = frozenset({"function_body", "function_expression_body"})
    int_kinds = frozenset({"decimal_integer_literal", "hex_integer_literal"})
    literal_kinds = frozenset({"list_literal", "set_or_map_literal"})
    wrapper_kinds = frozenset({"parenthesized_expression"})
    pass_through = frozenset(
        {"where", "map", "expand", "toList", "toSet", "keys", "values", "entries", "iterator"}
    )
    caps = frozenset({"take"})
    size_names = frozenset({"length"})
    cursor_advances = frozenset({"moveNext"})
    change_kinds = frozenset({"assignment_expression", "postfix_expression"})
    grow_methods = frozenset({"add", "addAll", "insert", "putIfAbsent"})
    conditional_kinds = frozenset(
        {
            "if_statement",
            "switch_statement",
            "switch_expression",
            "switch_statement_case",
            "for_statement",
            "while_statement",
            "do_statement",
            "try_statement",
            "catch_clause",
            "conditional_expression",
        }
    )

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        if loop.type == "for_statement":
            return _for_loop_header(loop)
        if loop.type in ("while_statement", "do_statement"):
            cond = loop.child_by_field_name("condition") or next(
                (c for c in loop.children if c.type == "parenthesized_expression"), None
            )
            return "cursor", _chain_node_list([cond] if cond is not None else [])
        return "each", None

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        if not _is_call_selector(node):
            return None
        sibs, idx = _chain(node)
        if idx <= 0:
            return None
        prev = sibs[idx - 1]
        member = _selector_member(prev)
        if member is not None:
            method, receiver = member, (sibs[idx - 2] if idx >= 2 else None)
        elif prev.type == "identifier" and prev.text:
            method, receiver = prev.text.decode("utf-8", "replace"), None
        else:
            return None
        return method, receiver, _call_args(node)

    def member_parts(self, node: Node) -> tuple[str, Node] | None:
        member = _selector_member(node)
        if member is None:
            return None
        sibs, idx = _chain(node)
        if idx <= 0:
            return None
        if idx + 1 < len(sibs) and _is_call_selector(sibs[idx + 1]):
            return None
        return member, sibs[idx - 1]

    def _iterable(self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        node = _chain_node_list([expr] if expr is not None else [])
        if node is not None:
            member = self.member_parts(node)
            if member is not None and member[0] == "values" and _is_type_name(member[1]):
                return "constant", "bounded"
        return super()._iterable(node, loop, probe, hop=hop)

    def is_literal(self, node: Node) -> bool:
        if not super().is_literal(node):
            return False
        return not any(c.type in _DART_NON_LITERAL_ENTRIES for c in node.named_children)

    def special_call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
    ) -> Source | None:
        method, receiver, args = parts
        if method == "values" and not args and _is_type_name(receiver):
            return "constant", "bounded"
        if (
            method == "generate"
            and text_of(receiver) == "List"
            and bool(args)
            and self._unwrap(args[0]).type in self.int_kinds
        ):
            return "constant", "bounded"
        if method == "listSync" and receiver is not None:
            recursive = bool(_DART_RECURSIVE_ARG.search(call.text or b""))
            grows = recursive and self._dart_walks_repo(receiver, loop)
            return "listing", "grows_with_data" if grows else None
        return None

    def _dart_walks_repo(self, receiver: Node, loop: Node) -> bool:
        sibs, idx = _chain(receiver)
        roots = sibs[: idx + 1] if idx >= 0 else [receiver]
        names = [n for root in roots for n in self._walk(root) if n.type in self.name_kinds]
        for node in list(names):
            value = self.reaching_value(loop, node.text or b"")
            if value is not None:
                v_sibs, v_idx = _chain(value)
                v_roots = v_sibs[: v_idx + 1] if v_idx >= 0 else [value]
                names.extend(n for r in v_roots for n in self._walk(r) if n.type in self.name_kinds)
        return any(lex.names_repo_root(text_of(n)) for n in names)

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        if node.type == "initialized_variable_definition":
            return _var_binding(node)
        if node.type == "assignment_expression":
            return _assign_binding(node)
        return []

    def declared_names(self, node: Node) -> list[bytes]:
        if node.type in ("initialized_variable_definition", "for_loop_parts"):
            name = node.child_by_field_name("name")
            return [name.text] if name is not None and name.text else []
        if node.type == "formal_parameter":
            idents = [c for c in node.children if c.type == "identifier" and c.text]
            return [idents[-1].text] if idents else []
        return []

    def param_names(self, loop: Node) -> set[bytes]:
        scope = self._scope(loop)
        sig = scope.prev_named_sibling if scope is not None else None
        if sig is None:
            return set()
        out: set[bytes] = set()
        for fp in self._walk(sig, prune=self.function_kinds):
            if fp.type == "formal_parameter":
                idents = [c for c in fp.children if c.type == "identifier" and c.text]
                if idents:
                    out.add(idents[-1].text)
        return out


class DartPerfDialect(DartLoopSources):
    language = "dart"
    markers = frozenset(
        {
            "io_in_loop",
            "serial_await_in_loop",
            "string_concat_in_loop",
            "resource_construction_in_loop",
        }
    )
    string_literal_kinds = frozenset({"string_literal"})
    aug_assign_kinds = frozenset({"assignment_expression"})

    # -- callee extraction over selector chains -------------------------------

    def callee_method_name(self, call_node: Node) -> str | None:
        if not _is_call_selector(call_node):
            return None
        sibs, idx = _chain(call_node)
        if idx <= 0:
            return None
        prev = sibs[idx - 1]
        member = _selector_member(prev)
        if member is not None:
            return member
        if prev.type == "identifier" and prev.text:
            # Bare call: ``foo(args)`` / constructor ``HttpClient()``.
            return prev.text.decode("utf-8", "replace")
        return None

    def callee_root_name(self, call_node: Node) -> str | None:
        if not _is_call_selector(call_node):
            return None
        sibs, idx = _chain(call_node)
        if idx <= 0:
            return None
        head = sibs[0]
        if head.type == "identifier" and head.text:
            return head.text.decode("utf-8", "replace")
        if head.type == "this":
            return "this"
        return None

    def callee_is_attribute(self, call_node: Node) -> bool:
        sibs, idx = _chain(call_node)
        if idx <= 0:
            return False
        return _selector_member(sibs[idx - 1]) is not None

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
        return _rhs_is_stringish(node, self.string_literal_kinds)


DIALECT = DartPerfDialect()
