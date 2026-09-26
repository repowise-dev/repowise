"""TypeScript / JavaScript ``PerfDialect``.

Extracted verbatim from the original ``_ts_sink_kind`` (``io_boundaries.py``)
and the TS branches of the walker (``_has_async_modifier`` and the
``_TS_STRING_KINDS`` string-concat predicate). One instance serves both
``typescript`` / ``tsx`` and ``javascript`` / ``jsx`` (identical call grammar).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from ..loop_facts import BatchForm
from .python import HTTP_VERBS
from .ts_js_markers import TsJsMarkerHooks, first_named_child, identifier_name

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import LoopMagnitude, SinkProbe

# TypeScript / JavaScript. Only DISTINCTIVE method names are trusted without
# import resolution: bare create/update/delete/count/exec collide hard with
# Map/Set/Array/RegExp and are pure noise. Generic fs/subprocess verbs are only
# trusted when the root binds to an imported node:fs / child_process.
PRISMA_METHODS: frozenset[str] = frozenset(
    {
        "findMany",
        "findUnique",
        "findFirst",
        "findUniqueOrThrow",
        "findFirstOrThrow",
        "createMany",
        "updateMany",
        "deleteMany",
        "upsert",
        "aggregate",
        "groupBy",
    }
)
TS_FS_METHODS: frozenset[str] = frozenset(
    {"readFileSync", "writeFileSync", "appendFileSync", "readdirSync"}
)
# Synchronous execution-sink verbs across the TS I/O ecosystems. Used to gate a
# call on an imported I/O package: a *round-trip* method (or an awaited call),
# never a sync helper like ``axios.isCancel`` / ``axios.create`` / a query
# *builder* like ``.where()``. Async sinks (``axios.get``, prisma queries,
# ``fs/promises`` reads) are caught by the ``awaited`` arm instead.
TS_SINK_METHODS: frozenset[str] = (
    HTTP_VERBS
    | PRISMA_METHODS
    | TS_FS_METHODS
    | frozenset(
        {
            "query",
            "execute",
            "exec",
            "execSync",
            "spawn",
            "spawnSync",
            "execFile",
            "execFileSync",
            "raw",
            "$queryRaw",
            "$executeRaw",
        }
    )
)

_TS_STRING_KINDS: frozenset[str] = frozenset({"string", "template_string"})
_TS_AUG_ASSIGN_KINDS: frozenset[str] = frozenset({"augmented_assignment_expression"})

# A function/lambda scope: the boundary for the reaching-assignment walk and the
# "not a loop exit" skip for break/return/throw inside a nested closure (a
# callback's own ``return`` doesn't exit the loop).
_FN_KINDS: frozenset[str] = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition"}
)

# The prisma point calls a loop can batch, and the bulk call each becomes.
_PRISMA_BULK_FORMS: dict[str, str] = {
    "findUnique": "findMany",
    "findFirst": "findMany",
    "delete": "deleteMany",
}

# A concurrency limiter, named by convention (``limit`` / ``this.sem``), or by the
# method async-mutex (``runExclusive``) and bottleneck (``schedule``) run work through.
_LIMITER_NAME_RE = re.compile(r"(?i)(sem|semaphore|limiter|limit|mutex)$")
_LIMITER_METHODS: frozenset[str] = frozenset({"runExclusive", "schedule"})

_ASSIGNMENT_FIELDS: dict[str, tuple[str, str]] = {
    "variable_declarator": ("name", "value"),
    "assignment_expression": ("left", "right"),
}


def _enclosing_scope(loop: Node) -> Node:
    """The function around *loop*, else the loop's own parent."""
    scope = loop.parent
    while scope is not None and scope.type not in _FN_KINDS:
        scope = scope.parent
    return scope or loop.parent or loop


def _unwrap(node: Node) -> Node:
    """Peel await / parens: ``(await x()).y`` needs both hops stripped to
    reach the call ``x()`` itself."""
    cur = node
    while cur.type in ("await_expression", "parenthesized_expression"):
        inner = first_named_child(cur)
        if inner is None:
            return cur
        cur = inner
    return cur


def _callee_object(call: Node) -> Node | None:
    """``x`` in ``x.method(...)``."""
    fn = call.child_by_field_name("function")
    return fn.child_by_field_name("object") if fn is not None else None


def _pair_key_is(node: Node, key: bytes) -> bool:
    return node.type == "pair" and (node.child_by_field_name("key") or node).text == key


def _option_value(call: Node, key: bytes) -> Node | None:
    """The value under *key* in the call's leading ``{ ... }`` options argument."""
    args = call.child_by_field_name("arguments")
    options = first_named_child(args) if args is not None else None
    if options is None or options.type != "object":
        return None
    return next(
        (pair.child_by_field_name("value") for pair in options.children if _pair_key_is(pair, key)),
        None,
    )


def _is_data_projection(node: Node) -> bool:
    """``response.data``: the body of whatever *response* resolves to."""
    prop = node.child_by_field_name("property")
    return node.type == "member_expression" and prop is not None and prop.text == b"data"


def _slice_is_bounded(call: Node) -> bool:
    """``.slice(a, N)`` where N is an integer literal or an ALL_CAPS named
    constant — a constant-width read, not the whole collection."""
    args = call.child_by_field_name("arguments")
    named = [c for c in args.children if c.is_named] if args is not None else []
    if len(named) != 2:
        return False
    end = named[1]
    if end.type == "number":
        return True
    name = identifier_name(end)
    return name is not None and name.isupper()


def _sole_where_pair(call: Node) -> tuple[Node, Node] | None:
    """``(key, value)`` of a ``where`` object that filters on exactly one field."""
    where = _option_value(call, b"where")
    if where is None or where.type != "object":
        return None
    pairs = [c for c in where.children if c.type == "pair"]
    if len(pairs) != 1:
        return None
    key, value = pairs[0].child_by_field_name("key"), pairs[0].child_by_field_name("value")
    return (key, value) if key is not None and value is not None else None


def _assigned_value(node: Node, name: bytes) -> Node | None:
    """The value *node* binds to the bare identifier *name*, if it is such a binding."""
    fields = _ASSIGNMENT_FIELDS.get(node.type)
    if fields is None:
        return None
    target, value = (node.child_by_field_name(field) for field in fields)
    if target is None or target.type != "identifier" or target.text != name:
        return None
    return value


class TsJsPerfDialect(TsJsMarkerHooks):
    language = "typescript"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "resource_construction_in_loop",
            "serial_await_in_loop",
            "membership_test_against_list_in_loop",
            # Centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # JS/TS-specific anti-patterns.
            "json_parse_in_loop",
            "array_spread_in_reduce",
        }
    )

    string_literal_kinds = _TS_STRING_KINDS
    aug_assign_kinds = _TS_AUG_ASSIGN_KINDS

    # Only ``for ... of`` / ``for ... in`` multiply over a collection. C-style
    # ``for (;;)``, ``while`` and ``do`` are cursors (pagination / polling), so
    # they do not make an inner sink a nested O(n*m) round-trip.
    _ITERATION_LOOP_KINDS: frozenset[str] = frozenset({"for_in_statement", "for_of_statement"})

    def is_iteration_loop(self, node: Node) -> bool:
        return node.type in self._ITERATION_LOOP_KINDS

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type not in self._ITERATION_LOOP_KINDS:
            return None
        right = node.child_by_field_name("right")
        if right is not None and right.type in ("identifier", "member_expression"):
            return self._dotted_path(right)
        return None

    def is_constant_loop(self, node: Node) -> bool:
        """True if a ``for...of`` / ``for...in`` iterates a compile-time-constant
        bound: an inline **array literal** (``for (const p of ["/a", "/b"])`` —
        the author enumerated a fixed set, so there is no data-dependent N+1
        blow-up) or an **ALL_CAPS** named constant (``for (const f of
        DREAMS_FILENAMES)``). Mirrors the Python dialect's literal-collection
        skip. C-style ``for (;;)`` / ``while`` / ``do`` are cursors — never
        constant here (they are pagination / polling)."""
        if node.type not in self._ITERATION_LOOP_KINDS:
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type == "array":
            return True
        name = identifier_name(right)
        return name is not None and name.isupper() and len(name) > 1

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
        if method == "fetch":  # the ``fetch(...)`` global — no import to resolve
            return "network"
        root_kind = io_names.get(root)
        if root_kind is not None:
            # A call on an imported I/O package (``axios.get`` / ``db.query``) is
            # a sink only when it is a known round-trip verb or is awaited — NOT
            # a sync helper (``axios.isCancel`` / ``axios.create``) or a query
            # builder (``.where()`` / ``.select()``), which over-fired before
            # this gate.
            if method in TS_SINK_METHODS or awaited:
                return root_kind
            return None
        # Distinctive prisma-client verbs, but only as a MEMBER call. A prisma
        # query is always reached through the client (``prisma.user.aggregate``),
        # so a bare ``aggregate(xs)`` / ``groupBy(xs)`` / ``upsert(x)`` is an
        # ordinary local function and was being reported as a database round
        # trip -- an N+1 finding on pure arithmetic, in code with no database.
        if is_attribute and method in PRISMA_METHODS:
            return "db"
        if method in TS_FS_METHODS:  # distinctive sync-fs verbs
            return "filesystem"
        return None

    # -- chunked-loop detection ------------------------------------------------

    def is_chunked_loop(self, node: Node) -> bool:
        """True when the loop already walks its data one chunk at a time.

        Two idioms: a C-style counter with a non-1 step (``i += CHUNK``), and a
        ``for...of`` over a ``chunk(xs, n)`` / lodash ``_.chunk(xs, n)`` call.
        """
        if node.type == "for_statement":
            return self._steps_by_chunk(node.child_by_field_name("increment"))
        if node.type in self._ITERATION_LOOP_KINDS:
            right = node.child_by_field_name("right")
            if right is None or right.type != "call_expression":
                return False
            return self.callee_method_name(right) == "chunk"
        return False

    # -- promotion facts (perf/loop_facts.py) ----------------------------------

    sequence_appends = frozenset({"push", "unshift", "splice"})
    await_kind = "await_expression"
    key_hops: ClassVar[dict[str, str]] = {
        "member_expression": "object",
        "subscript_expression": "object",
    }
    binding_fields: ClassVar[dict[str, str]] = {
        "variable_declarator": "name",
        "assignment_expression": "left",
        "augmented_assignment_expression": "left",
        "for_in_statement": "left",
    }
    branch_kinds = frozenset(
        {
            "if_statement", "try_statement", "switch_statement", "while_statement",
            "do_statement", "ternary_expression", "binary_expression",
        }
    )
    exit_kinds = frozenset(
        {"break_statement", "continue_statement", "return_statement", "throw_statement"}
    )
    scope_kinds = _FN_KINDS

    def _magnitude_of_expr(
        self, expr: Node | None, probe: SinkProbe, depth: int = 0
    ) -> LoopMagnitude | None:
        """Whether *expr* is provably a growing / bounded source, else None."""
        if expr is None or depth > 4:
            return None
        node = _unwrap(expr)
        if node.type == "call_expression":
            return self._call_magnitude(node, probe, depth)
        if node.type == "binary_expression" and any(c.type in ("||", "??") for c in node.children):
            # ``(await q).data ?? []``: the fallback is empty, the read is not.
            inner = node.child_by_field_name("left")
        elif _is_data_projection(node):
            inner = node.child_by_field_name("object")
        else:
            return None
        return self._grows(self._magnitude_of_expr(inner, probe, depth + 1))

    def _call_magnitude(self, call: Node, probe: SinkProbe, depth: int) -> LoopMagnitude | None:
        method = self.callee_method_name(call) or ""
        if method == "slice":
            return "bounded" if _slice_is_bounded(call) else None
        if probe(call) in ("db", "network"):
            # A read capped in the query (``take: n``) is as large as its cap.
            capped = any(_pair_key_is(n, b"take") for n in self._walk(call))
            return None if capped else "grows_with_data"
        if method == "json":
            # ``fetch(url).json()`` / ``(await sink()).json()`` — a
            # projection of whatever the receiver resolves to.
            return self._grows(self._magnitude_of_expr(_callee_object(call), probe, depth + 1))
        return None

    def _reaching_assignment(self, loop: Node, name: bytes) -> Node | None:
        """The value of the last ``name = ...`` before *loop* in its function."""
        last: tuple[int, Node] | None = None
        for node in self._walk(_enclosing_scope(loop), prune=_FN_KINDS):
            if node.end_byte > loop.start_byte:
                continue
            value = _assigned_value(node, name)
            if value is not None and (last is None or node.start_byte > last[0]):
                last = (node.start_byte, value)
        return last[1] if last is not None else None

    def loop_magnitude(self, loop: Node, probe: SinkProbe) -> LoopMagnitude | None:
        if not self.is_iteration_loop(loop):
            return None
        right = loop.child_by_field_name("right")
        magnitude = self._magnitude_of_expr(right, probe)
        if magnitude is None and right is not None and identifier_name(right):
            return self._magnitude_of_expr(self._reaching_assignment(loop, right.text), probe)
        return magnitude

    def _awaited_wrapper_call(self, closure: Node) -> Node | None:
        """The awaited call *closure* is passed to (``await limit(() => f(x))``).

        Awaited, the closure finishes before the loop moves on, so it is the loop
        body. Stored, returned or not awaited, it runs later and is not.
        """
        args = closure.parent
        if closure.type != "arrow_function" or args is None or args.type != "arguments":
            return None
        call = args.parent
        if call.type != "call_expression" or not self.is_awaited(call):
            return None
        fn = call.child_by_field_name("function")
        if fn is None or fn.type not in ("identifier", "member_expression"):
            return None
        return call

    def _limiter_of(self, closure: Node) -> str | None:
        """The limiter *closure* runs through. A curried ``pLimit(2)(...)`` builds a
        fresh limiter per call, which bounds nothing, so the callee must be named one."""
        call = self._awaited_wrapper_call(closure)
        if call is None:
            return None
        name = self.callee_method_name(call) or ""
        if not (_LIMITER_NAME_RE.search(name) or name in _LIMITER_METHODS):
            return None
        fn = call.child_by_field_name("function")
        return (fn.text or b"").decode() or None

    def runs_in_place(self, closure: Node) -> bool:
        return self._limiter_of(closure) is not None

    def is_awaited(self, node: Node) -> bool:
        # ``await limit(() => fetch(x))`` awaits what the closure's expression body returns.
        if super().is_awaited(node):
            return True
        parent = node.parent
        if parent is None or parent.child_by_field_name("body") != node:
            return False
        return self._limiter_of(parent) is not None

    def concurrency_bound(self, sink: Node, loop: Node) -> str | None:
        if self._exits_early(self.loop_body(loop) or loop):
            return None
        cur = sink.parent
        while cur is not None and cur != loop:
            limiter = self._limiter_of(cur)
            if limiter:
                return limiter
            cur = cur.parent
        return None

    def batch_form(self, sink: Node, loop: Node, probe: SinkProbe) -> BatchForm | None:
        """Prisma ``m.findUnique/findFirst/delete({ where: { f: key } })`` -> its bulk form."""
        element = self._loop_element(loop)
        point_call = self._prisma_point_call(sink)
        if element is None or point_call is None:
            return None
        receiver, method = point_call
        body = self.loop_body(loop) or loop
        if self._appends_to_iterable(loop, body):
            return None  # a worklist: its keys are not known before the loop
        field = self._where_key(sink, element)
        if field is None or self._reads_loop_local(sink, body):
            return None
        text = (receiver.text or b"").decode()
        return BatchForm(
            f"{text}.{_PRISMA_BULK_FORMS[method]}({{ where: {{ {field}: {{ in: keys }} }} }})",
            self._batch_equivalent(sink, body, probe),
        )

    def _loop_element(self, loop: Node) -> Node | None:
        """The identifier a ``for...of`` / ``for...in`` binds each element to."""
        target = loop.child_by_field_name("left")
        if not self.is_iteration_loop(loop) or identifier_name(target) is None:
            return None
        return target

    def _prisma_point_call(self, sink: Node) -> tuple[Node, str] | None:
        """``(receiver, method)`` of a ``m.findUnique/findFirst/delete(...)`` member call."""
        fn = sink.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        receiver, method = fn.child_by_field_name("object"), self.callee_method_name(sink)
        if receiver is None or method not in _PRISMA_BULK_FORMS:
            return None
        return receiver, method

    def _appends_to_iterable(self, loop: Node, body: Node) -> bool:
        """The body pushes onto the collection the loop walks."""
        iterable = self.loop_iterable_name(loop)
        return any(
            node.type == "call_expression"
            and self.callee_root_name(node) == iterable
            and self.callee_method_name(node) in self.sequence_appends
            for node in self._walk(body)
        )

    def _where_key(self, sink: Node, element: Node) -> str | None:
        """The field of a one-field ``where`` whose value is the loop *element*,
        when the sink names that element exactly once."""
        refs = [n for n in self._walk(sink) if n.type == "identifier" and n.text == element.text]
        pair = _sole_where_pair(sink)
        if len(refs) != 1 or pair is None or not self._is_key_of(pair[1], refs[0]):
            return None
        return (pair[0].text or b"").decode() or None

    def _batch_equivalent(self, sink: Node, body: Node, probe: SinkProbe) -> bool:
        """Whether the bulk call does exactly what the loop's per-row calls did."""
        if not self._only_io_in_body(sink, body, probe):
            return False
        method = self.callee_method_name(sink)
        if method == "delete":
            # Batched, a delete also removes rows a skipped iteration would have left.
            return self._unconditional(sink, body)
        return method == "findUnique" and not self._builds_in_order(body)


DIALECT = TsJsPerfDialect()
