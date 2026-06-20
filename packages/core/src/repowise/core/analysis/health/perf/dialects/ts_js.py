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
        }
    )

    string_literal_kinds = _TS_STRING_KINDS
    aug_assign_kinds = _TS_AUG_ASSIGN_KINDS

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
        return None

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
