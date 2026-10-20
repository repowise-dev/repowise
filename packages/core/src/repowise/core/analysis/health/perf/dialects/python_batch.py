"""Whether a per-item Python query in a loop has a single batched form."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..loop_facts import BatchForm
from .python_lexicon import (
    _PY_BUILTINS,
    _PY_ONE_ROW,
    _PY_PER_KEY_LIMITS,
    _PY_SCOPES,
    _PY_SEM_NAME_RE,
    _PY_WRITES,
)
from .python_nodes import _string_content

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import SinkProbe


class PythonBatchForms:
    """Batched forms and concurrency bounds. Mixed into :class:`~.python.PythonPerfDialect`."""

    def batch_form(self, sink: Node, loop: Node, probe: SinkProbe) -> BatchForm | None:
        target = loop.child_by_field_name("left")
        if loop.type != "for_statement" or target is None or target.type != "identifier":
            return None
        body = self.loop_body(loop) or loop
        if self._appends_to_iterable(body, self.loop_iterable_name(loop)):
            return None  # a worklist: its keys are not known before the loop
        methods = {self.callee_method_name(n) for n in self._walk(sink) if n.type == "call"}
        call = self._batch_call(sink, body, target, methods)
        if call is None:
            return None
        return BatchForm(call, self._batch_equivalent(sink, body, probe, methods))

    def _appends_to_iterable(self, body: Node, iterable: str | None) -> bool:
        return any(
            node.type == "call"
            and self.callee_is_attribute(node)
            and self.callee_root_name(node) == iterable
            and self.callee_method_name(node) in self.sequence_appends
            for node in self._walk(body)
        )

    def _batch_call(
        self, sink: Node, body: Node, target: Node, methods: set[str | None]
    ) -> str | None:
        """The bulk filter for a read keyed once on the loop target and nothing loop-local."""
        refs = [n for n in self._walk(sink) if n.type == "identifier" and n.text == target.text]
        if len(refs) != 1 or methods & _PY_WRITES or self._reads_loop_local(sink, body):
            return None
        return self._keyed_filter(sink, refs[0], methods)

    def _batch_equivalent(
        self, sink: Node, body: Node, probe: SinkProbe, methods: set[str | None]
    ) -> bool:
        if "delete" in methods:
            # Batched, a delete also removes rows a skipped iteration would have left.
            kept = self._unconditional(sink, body)
        else:
            kept = not self._builds_in_order(body)
        if self._orm_get(sink) is not None:
            # ``get`` may answer from the session's identity map, which no syntax
            # shows, so the bulk form is named, never proven.
            kept = False
        return (
            kept
            and not methods & _PY_PER_KEY_LIMITS
            and not self._limited_downstream(sink, body)
            and self._only_io_in_body(sink, body, probe)
            and not self._calls_for_effect(sink, body)
        )

    def _calls_for_effect(self, sink: Node, body: Node) -> bool:
        """Another statement calls something this function did not build as a list, set
        or dict (``run_task(doc)``, ``session.add(r)``, ``t = task.delay(d)``), which may
        write what the batched read would read before it ran."""
        for node in self._walk(body, prune=_PY_SCOPES):
            if node.type == "expression_statement":
                call, assigned = node.named_children[0], False
            elif node.type == "assignment":
                call, assigned = node.child_by_field_name("right"), True
            else:
                continue
            if call is not None and call.type == "await":
                call = next((c for c in call.children if c.is_named), None)
            if call is None or call.type != "call" or self._within(call, sink):
                continue
            if self._has_effect(call, assigned=assigned):
                return True
        return False

    def _has_effect(self, call: Node, *, assigned: bool) -> bool:
        """Not ``seen.add(x)`` on a local collection, and, when its result is kept, not a
        builtin (``str(r.id)``) or a method on a local (``res.first()``)."""
        if not self.callee_is_attribute(call):
            return not (assigned and self.callee_method_name(call) in _PY_BUILTINS)
        before = self.bindings_before(call, (self.callee_root_name(call) or "").encode())
        if not before:
            return True
        rhs = before[-1][1]
        return not (assigned or self._rhs_is_list(rhs) or self._rhs_is_nonlist_container(rhs))

    def _limited_downstream(self, sink: Node, body: Node) -> bool:
        """The result is cut to one row after the call (``(await q).first()``,
        ``res = await q`` then ``res.scalar_one()``), which one IN query does not do."""
        names: set[bytes | None] = set()
        cur = sink
        while cur.parent is not None and cur.parent != body:
            cur = cur.parent
            if cur.type == "call" and self.callee_method_name(cur) in _PY_ONE_ROW:
                return True
            left = cur.child_by_field_name("left") if cur.type == "assignment" else None
            if left is not None and left.type == "identifier":
                names.add(left.text)
        return any(
            n.type == "call"
            and self.callee_method_name(n) in _PY_ONE_ROW
            and (self.callee_root_name(n) or "").encode() in names
            for n in self._walk(body)
        )

    def _keyed_filter(self, sink: Node, ref: Node, methods: set[str | None]) -> str | None:
        """The bulk filter when *sink* selects or deletes by equality on *ref*."""
        orm_get = self._orm_get(sink)
        if orm_get is not None and self._is_key_of(orm_get[1], ref):
            return f"select({orm_get[0]}).where(inspect({orm_get[0]}).primary_key[0].in_(keys))"
        for node in self._walk(sink):
            if node.type == "call" and self.callee_method_name(node) == "eq":
                column_arg = self._eq_column_arg(node, ref, methods)
                if column_arg is not None:
                    column = _string_content(column_arg)
                    return f'.in_("{column}", keys)' if column else None
            if node.type == "comparison_operator" and any(c.type == "==" for c in node.children):
                found = self._equality_filter(node, ref)
                if found is not None:
                    return found
        return None

    def _eq_column_arg(self, call: Node, ref: Node, methods: set[str | None]) -> Node | None:
        """The column string of supabase-py ``.table(t).select(...).eq("col", key)``."""
        args = [c for c in call.child_by_field_name("arguments").children if c.is_named]
        if (
            len(args) == 2
            and args[0].type == "string"
            and self._is_key_of(args[1], ref)
            and ("select" in methods) != ("delete" in methods)
        ):
            return args[0]
        return None

    def _equality_filter(self, comparison: Node, ref: Node) -> str | None:
        """SQLAlchemy ``.where(M.col == key)`` / ``.filter(M.col == key)``."""
        sides = [c for c in comparison.children if c.is_named]
        if len(sides) != 2:
            return None
        for column, key in (sides, sides[::-1]):
            path = self._dotted_path(column)
            if path and "." in path and self._is_key_of(key, ref):
                return f"{path}.in_(keys)"
        return None

    def concurrency_bound(self, sink: Node, loop: Node) -> str | None:
        if self._exits_early(self.loop_body(loop) or loop):
            return None
        cur = sink.parent
        while cur is not None and cur != loop:
            if cur.type == "with_statement" and any(c.type == "async" for c in cur.children):
                path = self._semaphore_entered(cur)
                if path is not None:
                    return path
            cur = cur.parent
        return None

    def _semaphore_entered(self, with_stmt: Node) -> str | None:
        """The semaphore-like name an ``async with`` enters. A constructor
        (``async with Semaphore(5)``) is fresh per iteration and bounds nothing."""
        clause = next((c for c in with_stmt.children if c.type == "with_clause"), None)
        for item in clause.children if clause is not None else ():
            value = item.child_by_field_name("value") if item.type == "with_item" else None
            path = self._dotted_path(value) if value is not None else None
            if path and _PY_SEM_NAME_RE.search(path.rsplit(".", 1)[-1]):
                return path
        return None
