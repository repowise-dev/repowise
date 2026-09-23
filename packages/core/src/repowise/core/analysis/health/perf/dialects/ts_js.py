"""TypeScript / JavaScript ``PerfDialect``.

Extracted verbatim from the original ``_ts_sink_kind`` (``io_boundaries.py``)
and the TS branches of the walker (``_has_async_modifier`` and the
``_TS_STRING_KINDS`` string-concat predicate). One instance serves both
``typescript`` / ``tsx`` and ``javascript`` / ``jsx`` (identical call grammar).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..loop_facts import BatchForm
from .base import BasePerfDialect
from .python import HTTP_VERBS

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


class TsJsPerfDialect(BasePerfDialect):
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
        if right.type == "identifier" and right.text is not None:
            name = right.text.decode("utf-8", "replace")
            if name.isupper() and len(name) > 1:
                return True
        return False

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
                and n.type in TsJsPerfDialect._FN_LITERAL_KINDS
                and TsJsPerfDialect._params_contain(n, name)
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

    # -- chunked-loop detection ------------------------------------------------

    def is_chunked_loop(self, node: Node) -> bool:
        """True when the loop already walks its data one chunk at a time.

        Two idioms: a C-style counter with a non-1 step (``i += CHUNK``), and a
        ``for...of`` over a ``chunk(xs, n)`` / lodash ``_.chunk(xs, n)`` call.
        """
        if node.type == "for_statement":
            inc = node.child_by_field_name("increment")
            if inc is None or inc.type != "augmented_assignment_expression":
                return False
            if not any(c.type == "+=" for c in inc.children):
                return False
            right = inc.child_by_field_name("right")
            if right is None:
                return False
            if right.type == "number" and right.text is not None:
                try:
                    return float(right.text.decode("utf-8", "replace")) != 1
                except ValueError:
                    return False
            return right.type == "identifier"  # a named step is not a literal 1
        if node.type in self._ITERATION_LOOP_KINDS:
            right = node.child_by_field_name("right")
            if right is None or right.type != "call_expression":
                return False
            return self.callee_method_name(right) == "chunk"
        return False

    # -- promotion facts (perf/loop_facts.py) ----------------------------------

    #: Filesystem listing calls whose result count scales with data on disk.
    #: A function/lambda scope: the boundary for the reaching-assignment walk
    #: (below it) and the "not a loop exit" skip for break/return/throw inside
    #: a nested closure (a callback's own ``return`` doesn't exit the loop).
    _FN_KINDS: frozenset[str] = frozenset(
        {"function_declaration", "function_expression", "arrow_function", "method_definition"}
    )
    _MUTATOR_METHODS: frozenset[str] = frozenset({"push", "unshift", "splice"})
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
    _PRISMA_BATCH_METHODS: frozenset[str] = frozenset({"findUnique", "findFirst", "delete"})

    @staticmethod
    def _unwrap(node: Node) -> Node:
        """Peel await / parens: ``(await x()).y`` needs both hops stripped to
        reach the call ``x()`` itself."""
        cur = node
        while cur.type in ("await_expression", "parenthesized_expression"):
            inner = next((c for c in cur.children if c.is_named), None)
            if inner is None:
                return cur
            cur = inner
        return cur

    def _magnitude_of_expr(
        self, expr: Node | None, probe: SinkProbe, depth: int = 0
    ) -> LoopMagnitude | None:
        """Whether *expr* is provably a growing / bounded source, else None."""
        if expr is None or depth > 4:
            return None
        node = self._unwrap(expr)
        if node.type == "binary_expression" and any(c.type in ("||", "??") for c in node.children):
            # ``(await q).data ?? []``: the fallback is empty, the read is not.
            return self._grows(self._magnitude_of_expr(node.child_by_field_name("left"), probe, depth + 1))
        if node.type == "call_expression":
            method = self.callee_method_name(node) or ""
            if method == "slice":
                return "bounded" if self._slice_is_bounded(node) else None
            if probe(node) in ("db", "network"):
                # A read capped in the query (``take: n``) is as large as its cap.
                capped = any(
                    n.type == "pair" and (n.child_by_field_name("key") or n).text == b"take"
                    for n in self._walk(node)
                )
                return None if capped else "grows_with_data"
            if method == "json":
                # ``fetch(url).json()`` / ``(await sink()).json()`` — a
                # projection of whatever the receiver resolves to.
                fn = node.child_by_field_name("function")
                obj = fn.child_by_field_name("object") if fn is not None else None
                return self._grows(self._magnitude_of_expr(obj, probe, depth + 1))
            return None
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            if prop is not None and prop.text == b"data":
                inner = self._magnitude_of_expr(node.child_by_field_name("object"), probe, depth + 1)
                return self._grows(inner)
        return None

    @staticmethod
    def _slice_is_bounded(node: Node) -> bool:
        """``.slice(a, N)`` where N is an integer literal or an ALL_CAPS named
        constant — a constant-width read, not the whole collection."""
        args = node.child_by_field_name("arguments")
        named = [c for c in args.children if c.is_named] if args is not None else []
        if len(named) != 2:
            return False
        end = named[1]
        if end.type == "number":
            return True
        return end.type == "identifier" and bool(end.text) and end.text.decode(
            "utf-8", "replace"
        ).isupper()

    def _reaching_assignment(self, loop: Node, name: bytes) -> Node | None:
        """The value of the last ``name = ...`` before *loop* in its function."""
        scope = loop.parent
        while scope is not None and scope.type not in self._FN_KINDS:
            scope = scope.parent
        last: tuple[int, Node] | None = None
        for node in self._walk(scope or loop.parent or loop, prune=self._FN_KINDS):
            if node.end_byte > loop.start_byte:
                continue
            if node.type == "variable_declarator":
                target, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            elif node.type == "assignment_expression":
                target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
            else:
                continue
            named = target is not None and target.type == "identifier" and target.text == name
            if named and value is not None and (last is None or node.start_byte > last[0]):
                last = (node.start_byte, value)
        return last[1] if last is not None else None

    def loop_magnitude(self, loop: Node, probe: SinkProbe) -> LoopMagnitude | None:
        if not self.is_iteration_loop(loop):
            return None
        right = loop.child_by_field_name("right")
        magnitude = self._magnitude_of_expr(right, probe)
        if magnitude is None and right is not None and right.type == "identifier" and right.text:
            return self._magnitude_of_expr(self._reaching_assignment(loop, right.text), probe)
        return magnitude

    # No ``concurrency_bound``: a limiter wraps its await in a closure
    # (``limit(() => call())``), and the walker does not count a closure as the
    # loop body, so no hit could reach it.

    def batch_form(self, sink: Node, loop: Node, probe: SinkProbe) -> BatchForm | None:
        """Prisma ``m.findUnique/findFirst/delete({ where: { f: key } })`` -> its bulk form."""
        target = loop.child_by_field_name("left")
        fn = sink.child_by_field_name("function")
        if (
            not self.is_iteration_loop(loop)
            or target is None
            or target.type != "identifier"
            or fn is None
            or fn.type != "member_expression"
        ):
            return None
        receiver, method = fn.child_by_field_name("object"), self.callee_method_name(sink)
        if receiver is None or method not in self._PRISMA_BATCH_METHODS:
            return None
        body = self.loop_body(loop) or loop
        iterable = self.loop_iterable_name(loop)
        for node in self._walk(body):
            if (
                node.type == "call_expression"
                and self.callee_root_name(node) == iterable
                and self.callee_method_name(node) in self._MUTATOR_METHODS
            ):
                return None  # a worklist: its keys are not known before the loop
        refs = [n for n in self._walk(sink) if n.type == "identifier" and n.text == target.text]
        field = self._where_key(sink, refs[0]) if len(refs) == 1 else None
        if field is None or self._reads_loop_local(sink, body):
            return None
        text = (receiver.text or b"").decode()
        only_io = self._only_io_in_body(sink, body, probe)
        if method == "delete":
            # Batched, a delete also removes rows a skipped iteration would have left.
            return BatchForm(
                f"{text}.deleteMany({{ where: {{ {field}: {{ in: keys }} }} }})",
                only_io and self._unconditional(sink, body),
            )
        return BatchForm(
            f"{text}.findMany({{ where: {{ {field}: {{ in: keys }} }} }})",
            only_io and method == "findUnique",
        )

    def _where_key(self, sink: Node, ref: Node) -> str | None:
        """The field of a one-field ``where`` whose value is the loop element *ref*."""
        args = sink.child_by_field_name("arguments")
        options = next((c for c in args.children if c.is_named), None) if args else None
        if options is None or options.type != "object":
            return None
        where = next(
            (
                pair.child_by_field_name("value")
                for pair in options.children
                if pair.type == "pair" and (pair.child_by_field_name("key") or pair).text == b"where"
            ),
            None,
        )
        pairs = [c for c in where.children if c.type == "pair"] if where is not None else []
        if where is None or where.type != "object" or len(pairs) != 1:
            return None
        key, value = pairs[0].child_by_field_name("key"), pairs[0].child_by_field_name("value")
        if key is None or value is None or not self._is_key_of(value, ref):
            return None
        return (key.text or b"").decode() or None

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


DIALECT = TsJsPerfDialect()
