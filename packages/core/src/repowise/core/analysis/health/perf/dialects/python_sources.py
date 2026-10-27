"""What a Python loop walks, and how large it can grow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import loop_lexicon as lex
from ..loop_facts import LoopSource
from .python_lexicon import (
    _PY_CONSTANT_RHS,
    _PY_KEY_READS,
    _PY_LISTINGS,
    _PY_MODEL_NAME_RE,
    _PY_PASS_THROUGH,
    _PY_READ_CAPS,
    _PY_RESULT_PROJECTIONS,
    _PY_SQL_COMMENT_RE,
    _PY_SQL_LIMIT_RE,
    _PY_SQL_LITERAL_RE,
    _PY_STREAMS,
)
from .python_nodes import (
    _field_text,
    _fixed_set,
    _in_filter,
    _is_or,
    _module_int_constant,
    _scalar_literal,
    _slice_bounds,
    _string_value,
    _text,
    _value_refs,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import LoopIterates, LoopMagnitude, SinkProbe

    Source = tuple[LoopIterates, LoopMagnitude | None]


class PythonLoopSources:
    """Loop sources and magnitudes. Mixed into :class:`~.python.PythonPerfDialect`."""

    read_call_kinds = frozenset({"call"})

    def loop_source(self, loop: Node, probe: SinkProbe) -> LoopSource | None:
        if loop.type != "for_statement":
            return None
        right = loop.child_by_field_name("right")
        baseline = self._magnitude(right, loop, probe, hop=True) or "unknown"
        iterates, claim = self._source(right, loop, probe, hop=True)
        if iterates == "unknown" and baseline == "unknown":
            return None
        return self.with_read_tables(LoopSource(iterates, claim or baseline), right, loop, probe)

    def _source(self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        """What *expr* walks, and the magnitude that settles when it differs from
        what the iterable's syntax alone proves (``None``: keep that).

        *hop* allows one step through the name's last assignment before the loop.
        """
        if expr is None:
            return "unknown", None
        if expr.type in ("await", "parenthesized_expression"):
            inner = next((c for c in expr.children if c.is_named), None)
            return self._source(inner, loop, probe, hop=hop)
        if _is_or(expr):
            return self._source(expr.child_by_field_name("left"), loop, probe, hop=hop)
        if expr.type in ("identifier", "attribute"):
            return self._named_source(expr, loop, probe, hop=hop)
        if expr.type == "subscript" and self._slice_magnitude(expr) == "bounded":
            # A fixed-width slice bounds the trip count, not what is walked.
            walked = self._source(expr.child_by_field_name("value"), loop, probe, hop=hop)
            return walked[0], "bounded"
        if expr.type == "call":
            return self._call_source(expr, loop, probe, hop=hop)
        return "unknown", None

    def _named_source(self, expr: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        if lex.is_config_name(_text(expr)):
            # A registry grows with what users register: the name alone bounds nothing.
            return "config_table", None
        if expr.type == "identifier":
            return self._name_source(expr, loop, probe, hop=hop)
        return self._attribute_source(expr, loop, probe, hop=hop)

    def _attribute_source(self, expr: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        """``result.rows`` walks ``result``; ``self._TABLES`` bound to a literal is a constant."""
        attr = expr.child_by_field_name("attribute")
        name = (attr.text if attr is not None else b"") or b""
        if name.decode("utf-8", "replace") in _PY_RESULT_PROJECTIONS:
            return self._source(expr.child_by_field_name("object"), loop, probe, hop=hop)
        if self._literal_constant(expr, name):
            return "constant", "bounded"
        return "unknown", None

    def _name_source(self, name: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        rhs = self.reaching_value(loop, name.text or b"") if hop else None
        if rhs is not None:
            return self._source(rhs, loop, probe, hop=False)
        fn = loop.parent
        while fn is not None and fn.type != "function_definition":
            fn = fn.parent
        params = fn.child_by_field_name("parameters") if fn is not None else None
        if params is not None and _text(name) in self._param_names(params):
            return "parameter", None
        return "unknown", None

    def _call_source(self, call: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        method, fn, receiver, args = self._call_bits(call)
        if lex.is_config_name(self._dotted_path(fn) or "", callee=True):
            return "config_table", None
        if method == "range" and receiver is None:
            return self._range_source(call, args, loop, probe, hop=hop)
        walked = self._walked_arg_or_receiver(method, receiver, args)
        if walked is not None:
            return self._source(walked, loop, probe, hop=hop)
        if method in _PY_LISTINGS or method in _PY_STREAMS or (method == "open" and not receiver):
            return self._file_source(call, method, loop)
        if probe(call) in ("db", "network"):
            return self._query(call, loop)
        if receiver is not None and method in _PY_RESULT_PROJECTIONS:
            return self._source(receiver, loop, probe, hop=hop)
        return "unknown", None

    def _call_bits(self, call: Node) -> tuple[str, Node | None, Node | None, list[Node]]:
        """``(method, callee, receiver, positional args)`` of a call."""
        fn = call.child_by_field_name("function")
        receiver = fn.child_by_field_name("object") if fn is not None and fn.type == "attribute" else None
        arg_list = call.child_by_field_name("arguments")
        args = [c for c in arg_list.children if c.is_named] if arg_list is not None else []
        return self.callee_method_name(call) or "", fn, receiver, args

    @staticmethod
    def _walked_arg_or_receiver(
        method: str, receiver: Node | None, args: list[Node]
    ) -> Node | None:
        """``sorted(xs)`` / ``enumerate(xs)`` walk ``xs``; ``d.items()`` walks ``d``."""
        if method in _PY_PASS_THROUGH and args and args[0].type != "keyword_argument":
            return args[0]
        if receiver is not None and method in ("items", "values", "keys"):
            return receiver
        return None

    def _file_source(self, call: Node, method: str, loop: Node) -> Source:
        """A directory listing (grows when it walks the repository tree) or a stream."""
        if method in _PY_LISTINGS:
            return "listing", "grows_with_data" if self._walks_repo(call, method, loop) else None
        return "stream", None

    def _range_source(
        self, call: Node, args: list[Node], loop: Node, probe: SinkProbe, *, hop: bool
    ) -> Source:
        """``range(3)`` is a constant; ``range(0, len(ids), N)`` walks ``ids``."""
        if self._range_magnitude(call, loop, probe, hop=hop) == "bounded":
            return "constant", "bounded"
        stop = args[1] if len(args) > 1 else (args[0] if args else None)
        if stop is not None and stop.type == "call" and self.callee_method_name(stop) == "len":
            inner = [c for c in stop.child_by_field_name("arguments").children if c.is_named]
            if len(inner) == 1:
                return self._source(inner[0], loop, probe, hop=hop)
        return "unknown", None

    def read_tables(self, call: Node, nodes: list[Node]) -> tuple[str, ...]:
        """The tables its SQL texts read and every model-like name in the statement
        (``select(Repository)``, ``File.repository_id`` in a filter); ``()`` when a
        SQL text names no table or more than its FROM / JOIN list shows."""
        walked = [n for part in nodes for n in self._walk(part)]
        sql = [
            n.text or b""
            for n in walked
            if n.type == "string" and _PY_SQL_LITERAL_RE.match(_text(n))
        ]
        tables = [lex.read_sql_tables(text) for text in sql]
        if not all(tables):
            return ()
        models = [_text(n) for n in walked if n.type == "identifier" and _PY_MODEL_NAME_RE.match(_text(n))]
        return tuple(dict.fromkeys([*(t for found in tables for t in found), *models]))

    def _literal_constant(self, expr: Node, name: bytes) -> bool:
        """``self._TABLES`` where the file binds ``_TABLES`` once, to a literal collection."""
        text = name.decode("utf-8", "replace")
        if len(text.strip("_")) < 2 or not text.isupper():
            return False
        root = expr
        while root.parent is not None:
            root = root.parent
        rights = [
            n.child_by_field_name("right")
            for n in self._walk(root)
            if n.type == "assignment"
            and (left := n.child_by_field_name("left")) is not None
            and left.type == "identifier"
            and left.text == name
        ]
        return (
            len(rights) == 1
            and rights[0] is not None
            and rights[0].type in _PY_CONSTANT_RHS
            and all(_scalar_literal(item) for item in rights[0].named_children)
        )

    def _magnitude(
        self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        """Grows (a query result or a directory listing), bounded, or ``None``.

        *hop* allows one step through the name's last assignment before the
        loop; a parameter has none and stays unknown on purpose.
        """
        if expr is None:
            return None
        if expr.type in ("await", "parenthesized_expression"):
            inner = next((c for c in expr.children if c.is_named), None)
            return self._magnitude(inner, loop, probe, hop=hop)
        if expr.type == "identifier" and hop and expr.text:
            rhs = self.reaching_value(loop, expr.text)
            return self._magnitude(rhs, loop, probe, hop=False)
        if expr.type == "subscript":
            return self._slice_magnitude(expr)
        if _is_or(expr):
            # ``(await q.execute()).data or []``: the fallback is empty, the read is not.
            left = expr.child_by_field_name("left")
            return self._grows(self._magnitude(left, loop, probe, hop=hop))
        if expr.type == "attribute":
            return self._attribute_magnitude(expr, loop, probe, hop=hop)
        return self._call_magnitude(expr, loop, probe, hop=hop) if expr.type == "call" else None

    def _attribute_magnitude(
        self, expr: Node, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        attr = expr.child_by_field_name("attribute")
        if attr is None or (attr.text or b"").decode() not in _PY_RESULT_PROJECTIONS:
            return None
        return self._grows(self._magnitude(expr.child_by_field_name("object"), loop, probe, hop=hop))

    def _call_magnitude(
        self, expr: Node, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        method = self.callee_method_name(expr) or ""
        member = self.callee_is_attribute(expr)
        if method == "range" and not member:
            return self._range_magnitude(expr, loop, probe, hop=hop)
        if probe(expr) in ("db", "network"):
            # A read capped in the query is as large as its cap; a statement or result
            # built before the loop (``stmt = ....limit(n)``) is read one assignment back.
            names = [n.text for n in self._walk(expr) if n.type == "identifier"]
            parts = [expr, *filter(None, (self.reaching_value(loop, name) for name in names))]
            capped = any(self.is_cap(n, loop) for part in parts for n in self._walk(part))
            return None if capped else "grows_with_data"
        if member and method in _PY_RESULT_PROJECTIONS:
            receiver = expr.child_by_field_name("function").child_by_field_name("object")
            return self._grows(self._magnitude(receiver, loop, probe, hop=hop))
        return None

    def is_cap(self, node: Node, loop: Node) -> bool:
        """``.limit(n)`` and peers, a SQL string saying ``LIMIT n``, or a filter to a
        set of keys (:meth:`read_key_column`), which is as large as the keys are."""
        if node.type == "string":
            return bool(_PY_SQL_LIMIT_RE.search(_PY_SQL_COMMENT_RE.sub(b"", node.text or b"")))
        method = self.callee_method_name(node) if node.type == "call" else None
        if method in _PY_READ_CAPS:
            return True
        return node.type == "call" and self.read_key_column(node, loop) is not None

    def read_key_column(self, call: Node, anchor: Node) -> str | None:
        """``Model.col.in_(keys)`` and PostgREST ``.in_("col", keys)`` -> ``col``;
        ``filter_by(id=k)`` and ``.eq("id", k)`` -> ``id``. Keys a query yields
        (``.in_(select(...))``, or a name bound to one) grow with that table."""
        method = self.callee_method_name(call)
        arg_list = call.child_by_field_name("arguments")
        args = arg_list.named_children if arg_list is not None else []
        if method == "filter_by":
            named = {_field_text(a, "name") for a in args if a.type == "keyword_argument"}
            return "id" if "id" in named else None
        if method == "eq":
            return "id" if len(args) == 2 and _string_value(args[0]) == "id" else None
        found = _in_filter(call, args) if method == "in_" else None
        if found is None or not self._handed_in(found[1], anchor):
            return None
        return found[0]

    def _handed_in(self, keys: Node, anchor: Node) -> bool:
        """*keys* holds values given to the code, not ones a query yields or a fixed
        set of states (``in_(("a", "b"))``, ``in_(SOURCES)``) whose rows grow."""
        values = [
            self.reaching_value(anchor, ref.text or b"") if ref.type == "identifier" else None
            for ref in _value_refs(keys)
        ]
        if all(_fixed_set(value) for value in values):
            return False
        parts = [keys, *filter(None, values)]
        return not any(
            n.type == "call" and self.callee_method_name(n) in _PY_KEY_READS
            for part in parts
            for n in self._walk(part)
        )

    def _range_magnitude(
        self, call: Node, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        """``range(len(rows))`` grows with ``rows``; ``range(3)`` and ``range(MAX)``
        with ``MAX = 3`` bound once in the module are bounded."""
        args = [c for c in call.child_by_field_name("arguments").children if c.is_named]
        if not args:
            return None
        stop = args[1] if len(args) > 1 else args[0]
        if stop.type == "call" and self.callee_method_name(stop) == "len":
            inner = [c for c in stop.child_by_field_name("arguments").children if c.is_named]
            if len(inner) == 1:
                return self._grows(self._magnitude(inner[0], loop, probe, hop=hop))
            return None
        # A name alone (``MAX_RETRIES``, ``self.max_attempts``) proves nothing.
        return "bounded" if stop.type == "integer" or _module_int_constant(stop) else None

    def _slice_magnitude(self, subscript: Node) -> LoopMagnitude | None:
        """``xs[:5]`` / ``xs[:LIMIT]`` / ``xs[i:i + 4]``: a constant-width slice."""
        bounds = _slice_bounds(subscript)
        if bounds is None:
            return None
        start, stop = bounds
        if start is None:
            return "bounded" if stop.type == "integer" or _module_int_constant(stop) else None
        return "bounded" if self._offset_by_int(start, stop) else None

    def _offset_by_int(self, start: Node, stop: Node) -> bool:
        """*stop* is ``start + <integer>``."""
        if stop.type != "binary_operator":
            return False
        left, right = stop.child_by_field_name("left"), stop.child_by_field_name("right")
        same_start = left is not None and self._dotted_path(left) == self._dotted_path(start)
        return same_start and right is not None and right.type == "integer"
