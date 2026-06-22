"""TypeScript / JavaScript ``PerfDialect``.

Extracted verbatim from the original ``_ts_sink_kind`` (``io_boundaries.py``)
and the TS branches of the walker (``_has_async_modifier`` and the
``_TS_STRING_KINDS`` string-concat predicate). One instance serves both
``typescript`` / ``tsx`` and ``javascript`` / ``jsx`` (identical call grammar).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect
from .python import HTTP_VERBS

if TYPE_CHECKING:
    from tree_sitter import Node

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
            # Phase 7b — centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # Phase 7d — JS/TS-specific anti-patterns.
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
        if method in PRISMA_METHODS:  # distinctive prisma-client verbs
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
        # waste (use ``structuredClone``). Phase-7d gate: a BARE ``JSON.parse`` /
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
    def _spreads_name_in_collection(body: Node, name: str) -> bool:
        """True if *body* spreads ``name`` into an array / object literal
        (``[...name, x]`` / ``{...name}``) — the O(n^2) accumulator rebuild."""
        stack: list[Node] = [body]
        while stack:
            n = stack.pop()
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


DIALECT = TsJsPerfDialect()
