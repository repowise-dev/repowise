"""C# ``PerfDialect``.

Two flagship signals:

* **EF Core N+1** — an Entity Framework / Dapper / ADO.NET query inside a
  ``foreach``. The precision crux is LINQ: ``ToList`` / ``First`` / ``Where``
  are identical on an ``IQueryable`` (hits the DB) and an ``IEnumerable`` (pure
  memory). The unambiguous tell is the ``*Async`` family
  (``ToListAsync`` / ``FirstOrDefaultAsync`` / ``CountAsync`` …), which is
  EF-only and fires without a gate. Sync LINQ is gated on a file-level db
  import (MEDIUM precision in v1; receiver ``DbContext``-type binding deferred).
* **sync-over-async** — ``.Result`` / ``.Wait()`` / ``.GetAwaiter().GetResult()``
  inside an ``async`` method blocks the thread-pool thread. C# is the one new
  language with real ``async``/``await``, so ``blocking_sync_in_async`` ports
  here. ``.Wait()`` / ``.GetResult()`` are invocations (caught by
  :meth:`blocking_sync_api`); ``.Result`` is a property read — a non-call
  ``member_access_expression`` — caught by :meth:`async_blocking_member`.

C# call nodes are ``invocation_expression`` -> ``member_access_expression``
(fields ``expression`` / ``name``); the async marker is a ``modifier`` node
whose *text* is ``async`` (not a node of type ``async``), so :meth:`is_async_fn`
is overridden. .NET caches compiled regexes, so ``regex_compile_in_loop`` is
deliberately absent.

Static-blind, documented non-goal: EF navigation-property *lazy load* fires on
attribute access (no visible call) and is out of scope — we catch explicit
query calls in loops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# The EF ``*Async`` LINQ family — EF-only, so UNAMBIGUOUS db (no gate).
EF_ASYNC_METHODS: frozenset[str] = frozenset(
    {
        "ToListAsync",
        "ToArrayAsync",
        "ToDictionaryAsync",
        "FirstAsync",
        "FirstOrDefaultAsync",
        "SingleAsync",
        "SingleOrDefaultAsync",
        "LastAsync",
        "LastOrDefaultAsync",
        "FindAsync",
        "AnyAsync",
        "AllAsync",
        "CountAsync",
        "LongCountAsync",
        "SumAsync",
        "MinAsync",
        "MaxAsync",
        "AverageAsync",
        "ContainsAsync",
        "ElementAtAsync",
    }
)
# EF SaveChanges + raw SQL + bulk operations — unambiguous db.
EF_EXEC_METHODS: frozenset[str] = frozenset(
    {
        "SaveChanges",
        "SaveChangesAsync",
        "ExecuteSqlRaw",
        "ExecuteSqlRawAsync",
        "ExecuteSqlInterpolated",
        "ExecuteSqlInterpolatedAsync",
        "FromSqlRaw",
        "FromSqlInterpolated",
        "ExecuteUpdate",
        "ExecuteUpdateAsync",
        "ExecuteDelete",
        "ExecuteDeleteAsync",
    }
)
# ADO.NET command execution — unambiguous db.
ADO_METHODS: frozenset[str] = frozenset(
    {
        "ExecuteReader",
        "ExecuteReaderAsync",
        "ExecuteNonQuery",
        "ExecuteNonQueryAsync",
        "ExecuteScalar",
        "ExecuteScalarAsync",
        "OpenAsync",
    }
)
# Dapper extension verbs (collide with EF / plain method names) — gated on db.
DAPPER_METHODS: frozenset[str] = frozenset(
    {
        "Query",
        "QueryAsync",
        "QueryFirst",
        "QueryFirstAsync",
        "QueryFirstOrDefault",
        "QueryFirstOrDefaultAsync",
        "QuerySingle",
        "QuerySingleAsync",
        "QueryMultiple",
        "QueryMultipleAsync",
        "Execute",
        "ExecuteAsync",
    }
)
# Sync LINQ — identical on IQueryable (db) vs IEnumerable (memory). Gated on a
# file-level db import (MEDIUM precision, v1).
SYNC_LINQ_METHODS: frozenset[str] = frozenset(
    {
        "ToList",
        "ToArray",
        "First",
        "FirstOrDefault",
        "Single",
        "SingleOrDefault",
        "Last",
        "LastOrDefault",
        "ToDictionary",
    }
)
# HttpClient async round-trips — distinctive by name, unambiguous network.
HTTP_ASYNC_METHODS: frozenset[str] = frozenset(
    {
        "GetAsync",
        "PostAsync",
        "PutAsync",
        "DeleteAsync",
        "PatchAsync",
        "SendAsync",
        "GetStringAsync",
        "GetByteArrayAsync",
        "GetStreamAsync",
    }
)
# ``System.IO.File`` static round-trips (gate on the method + ``File`` receiver,
# NOT on a fs import — ``using System.IO;`` is everywhere).
FILE_METHODS: frozenset[str] = frozenset(
    {
        "ReadAllText",
        "ReadAllLines",
        "ReadAllBytes",
        "ReadAllTextAsync",
        "ReadAllLinesAsync",
        "WriteAllText",
        "WriteAllLines",
        "WriteAllBytes",
        "WriteAllTextAsync",
        "AppendAllText",
        "Open",
        "OpenRead",
        "OpenText",
        "OpenWrite",
        "Copy",
        "Move",
    }
)


# Heavy clients/connections to hoist out of a ``foreach``. Built via ``new X()``
# (an ``object_creation_expression``, which C# does NOT route through
# ``call_kinds``, so it reaches ``loop_stmt_marker``).
CSHARP_RESOURCE_CTORS: frozenset[str] = frozenset(
    {"HttpClient", "SqlConnection", "NpgsqlConnection", "MongoClient", "SqlConnectionStringBuilder"}
)


class CSharpPerfDialect(BasePerfDialect):
    language = "csharp"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "blocking_sync_in_async",
            "resource_construction_in_loop",
            "lock_in_loop",
            "serial_await_in_loop",
            # Phase 7b — centrality-gated / nesting-confidence markers + the
            # block-scoped lock→I/O case (``lock (x) {}`` is a held region).
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            "blocking_io_under_lock",
        }
    )

    def is_lock_scope(self, node: Node) -> bool:
        # ``lock (x) { ... }`` — the body is the held-lock region.
        return node.type == "lock_statement"

    # ``invocation_expression`` -> ``member_access_expression`` is the call
    # shape; add it so a member call reads as attribute-style.
    attribute_callee_kinds = BasePerfDialect.attribute_callee_kinds | frozenset(
        {"member_access_expression"}
    )
    string_literal_kinds = frozenset({"string_literal", "verbatim_string_literal"})
    aug_assign_kinds = frozenset({"assignment_expression"})

    def callee_method_name(self, call_node: Node) -> str | None:
        fn = call_node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "member_access_expression":
            nm = fn.child_by_field_name("name")
            if nm is not None and nm.text:
                return nm.text.decode("utf-8", "replace")
        if fn.type == "identifier" and fn.text:
            return fn.text.decode("utf-8", "replace")
        return None

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
        root_kind = io_names.get(root)
        db_ev = has_db_import or root_kind == "db"

        if method in EF_ASYNC_METHODS or method in EF_EXEC_METHODS or method in ADO_METHODS:
            return "db"
        if method in HTTP_ASYNC_METHODS:
            return "network"
        if root == "File" and method in FILE_METHODS:
            return "filesystem"
        if root == "Process" and method == "Start":
            return "subprocess"
        if is_attribute and db_ev and (method in DAPPER_METHODS or method in SYNC_LINQ_METHODS):
            return "db"
        return None

    def is_async_fn(self, node: Node) -> bool:
        # C# async is a ``modifier`` node whose text is ``async`` (vs Python/TS
        # where it is a child of *type* ``async``).
        return any(
            c.type == "modifier" and (c.text or b"").decode("utf-8", "replace") == "async"
            for c in node.children
        )

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        # The invocation forms of sync-over-async. ``.Result`` (a property read)
        # is handled by ``async_blocking_member`` instead.
        if method == "Wait":
            return ".Wait()"
        if method == "GetResult":
            return ".GetResult()"
        return None

    def async_blocking_member(self, node: Node) -> str | None:
        if node.type != "member_access_expression":
            return None
        nm = node.child_by_field_name("name")
        if nm is not None and nm.text and nm.text.decode("utf-8", "replace") == "Result":
            return ".Result"
        return None

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``Monitor.Enter(lock)`` — the call form of lock acquisition (the
        # ``lock(x){}`` statement form is handled in ``loop_stmt_marker``).
        if root == "Monitor" and method == "Enter":
            return "lock_in_loop"
        return None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``new HttpClient()`` per iteration — the canonical C# socket-exhaustion
        # bug — is an ``object_creation_expression`` (not an invocation).
        if node.type == "object_creation_expression":
            t = node.child_by_field_name("type")
            if t is not None and t.text:
                name = t.text.decode("utf-8", "replace").split(".")[-1]
                if name in CSHARP_RESOURCE_CTORS:
                    return "resource_construction_in_loop"
            return None
        # ``lock (gate) { ... }`` taken every iteration is a contention site.
        if node.type == "lock_statement":
            return "lock_in_loop"
        return None


DIALECT = CSharpPerfDialect()
