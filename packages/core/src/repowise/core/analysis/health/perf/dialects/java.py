"""Java ``PerfDialect``.

Flagship: the most infamous N+1 in industry — a Hibernate / Spring-Data
repository query inside a ``for`` loop (``for (id : ids) repo.findById(id)``).

The load-bearing seam is **callee extraction**: a Java call node *is* a
``method_invocation`` (fields ``object`` / ``name``) with no wrapping
member-access node, so the generic base extraction (which keys off a
``function`` field) does not work. This dialect supplies the Java arm. It also
handles ``object_creation_expression`` (``new FileInputStream(...)``) so a
constructor at a filesystem/network boundary inside a loop is caught.

New marker: ``regex_compile_in_loop`` (``Pattern.compile`` with no cached
``Pattern``). Java has no ``async``/``await`` syntax, so
``blocking_sync_in_async`` is intentionally absent (reactive-blocking would
need type information).

Static-blind, documented non-goal: Hibernate *lazy-load* N+1 fires on a getter
(no visible call), so it is invisible to a static call-shape detector — we
catch explicit repository/query calls in loops, not attribute-triggered lazy
loads. This caps recall, not precision.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# Spring-Data derived query methods: ``findByName`` / ``getAllByStatus`` /
# ``countByOwner`` … The name alone is not enough (``Settings.getByPrefix``,
# ``InetAddress.getByAddress`` and ``RoutingNodes.getByShardId`` are in-memory),
# so :func:`is_repository_call` also asks for a repository-named receiver or a
# db library import.
_SPRING_DERIVED = re.compile(r"^(find|get|query|count|exists|stream|read|delete)By[A-Z]")
_REPOSITORY_RECEIVER = re.compile(r"(?:repository|repo|dao)$", re.IGNORECASE)

# JDBC — unambiguous cursor/statement round-trips.
JDBC_METHODS: frozenset[str] = frozenset(
    {"executeQuery", "executeUpdate", "executeBatch", "executeLargeUpdate", "prepareStatement"}
)
# JPA / Hibernate EntityManager + Query.
JPA_METHODS: frozenset[str] = frozenset(
    {"getResultList", "getSingleResult", "getResultStream", "createNativeQuery"}
)
# Spring-Data CrudRepository finishers that are distinctive enough on their own.
SPRING_REPO_METHODS: frozenset[str] = frozenset({"saveAll", "findAllById", "deleteAllById"})
# Repository / JPA verbs other APIs share (Lucene ``IndexWriter.deleteAll``,
# ``ModuleFinder.findAll``, Kotlin ``Regex.findAll``, query builders'
# ``createQuery``): db only with the evidence :func:`is_repository_call` asks for.
_SHARED_REPO_METHODS: frozenset[str] = frozenset({"createQuery", "findAll", "deleteAll"})
# Statement, EntityManager and repository verbs: unambiguous, but only on a
# receiver (a bare ``executeQuery(ctx)`` is the enclosing class's own method).
RECEIVER_DB_METHODS: frozenset[str] = JDBC_METHODS | JPA_METHODS | SPRING_REPO_METHODS
# Spring RestTemplate — distinctive HTTP round-trip method names.
REST_TEMPLATE_METHODS: frozenset[str] = frozenset(
    {"getForObject", "postForObject", "getForEntity", "postForEntity", "exchange", "patchForObject"}
)
# WebClient ``.block*`` (Reactor) — gated on a network import (``.block`` alone
# is a generic Reactor finisher).
WEBCLIENT_BLOCK: frozenset[str] = frozenset({"block", "blockFirst", "blockLast"})
# ``java.nio.file.Files`` static round-trips.
FILES_METHODS: frozenset[str] = frozenset(
    {
        "readString",
        "readAllLines",
        "readAllBytes",
        "lines",
        "newInputStream",
        "newOutputStream",
        "newBufferedReader",
        "newBufferedWriter",
        "write",
        "copy",
        "move",
        "createFile",
        "list",
        "walk",
    }
)
# Constructors that open a filesystem / network boundary.
FS_CONSTRUCTORS: frozenset[str] = frozenset(
    {"FileInputStream", "FileOutputStream", "FileReader", "FileWriter", "RandomAccessFile"}
)
NET_CONSTRUCTORS: frozenset[str] = frozenset({"Socket", "ServerSocket"})
# Ambiguous DB verbs (collide with collections/builders/streams) — the riskiest
# stratum, gated on file-level db evidence. Kept deliberately small (the plan's
# find/get/execute/save/count) so generic verbs like ``list`` / ``stream`` /
# ``delete`` do not over-fire in a db-importing file.
AMBIGUOUS_DB: frozenset[str] = frozenset({"find", "get", "execute", "save", "count"})
# JDBC has no ``find`` / ``get`` / ``save`` / ``count`` verb, so JDBC evidence
# licenses only ``execute``; the rest need an ORM / repository library.
_JDBC_AMBIGUOUS_DB: frozenset[str] = frozenset({"execute"})
# The JDBC types whose import shows a file runs statements.
_JDBC_EXECUTION_TYPES: frozenset[str] = frozenset(
    {
        "Connection",
        "Statement",
        "PreparedStatement",
        "CallableStatement",
        "ResultSet",
        "DataSource",
        "DriverManager",
    }
)
# Names a ``java.sql`` / ``javax.sql`` import binds that run nothing: the
# package segments and the value, metadata and driver-side types. A file that
# imports only these (``JDBCType`` in a type mapper, ``DatabaseMetaData``
# constants) is no evidence that its ``map.get`` is a query. ``SQL*`` names
# (``SQLException``, ``SQLType``, ...) are matched by prefix.
_JDBC_PASSIVE_NAMES: frozenset[str] = frozenset(
    {
        "java",
        "javax",
        "sql",
        "JDBCType",
        "Types",
        "Date",
        "Time",
        "Timestamp",
        "DatabaseMetaData",
        "ResultSetMetaData",
        "ParameterMetaData",
        "DriverPropertyInfo",
        "BatchUpdateException",
        "DataTruncation",
        "Driver",
        "DriverAction",
        "Array",
        "Blob",
        "Clob",
        "NClob",
        "Ref",
        "RowId",
        "RowIdLifetime",
        "Struct",
        "Savepoint",
        "Wrapper",
        "ClientInfoStatus",
    }
)
_JDBC_PASSIVE_PREFIX = re.compile(r"^SQL[A-Z]")

# Heavy clients to hoist, not ``new`` each iteration. A ``new RestTemplate()``
# arrives as an ``object_creation_expression`` whose extracted "method" is the
# constructed type name (see ``callee_method_name``); ``getConnection`` is the
# DataSource / DriverManager connection-acquisition verb. Deliberately limited
# to framework-distinctive type names: a bare ``HttpClient`` is excluded because
# it collides with user-defined wrappers and Apache's ``HttpClient`` *interface*
# (resolved by last segment only, with no import gate) — a precision risk that
# outweighs the recall.
JAVA_RESOURCE_CTORS: frozenset[str] = frozenset({"RestTemplate", "OkHttpClient"})
JAVA_RESOURCE_METHODS: frozenset[str] = frozenset({"getConnection"})
# ``java.util.concurrent.locks.Lock`` acquisition (the contention side only).
JAVA_LOCK_METHODS: frozenset[str] = frozenset({"lock", "lockInterruptibly"})
# A function of these names takes the lock; ``while (true)`` / ``for (;;)`` is its retry loop.
JAVA_LOCK_ACQUIRE_FUNCTIONS: frozenset[str] = frozenset({"lock", "lockinterruptibly", "trylock"})
JAVA_SPIN_LOOP_HEADER = re.compile(r"while\s*\(\s*true\s*\)|for\s*\(\s*;\s*;\s*\)")
# ``Lists.partition`` / ``ListUtils.partition`` / ``Iterables.partition`` and
# hand-rolled peers, matched on the call's method name (the receiver is not
# gated: a local helper counts too).
_PARTITION_CALLS: frozenset[str] = frozenset({"partition", "chunked", "batches"})


def _is_passive_jdbc_name(name: str) -> bool:
    # An all-caps name is a static-imported constant (``Types.BIGINT``). No
    # library evidence is lost: every import also binds its package segments.
    return name in _JDBC_PASSIVE_NAMES or bool(_JDBC_PASSIVE_PREFIX.match(name)) or name.isupper()


def _db_import_evidence(io_names: dict[str, str]) -> str | None:
    """``"library"``, ``"jdbc"`` or ``None``: what this file's db imports show.

    A db library other than JDBC (Hibernate, JPA, Spring Data, Slick, ...) is
    ``"library"``; JDBC execution types alone are ``"jdbc"``; passive JDBC
    names (``JDBCType``, ``SQLException``) show nothing.
    """
    jdbc = False
    for name, kind in io_names.items():
        if kind != "db" or _is_passive_jdbc_name(name):
            continue
        if name not in _JDBC_EXECUTION_TYPES:
            return "library"
        jdbc = True
    return "jdbc" if jdbc else None


def ambiguous_db_verbs(io_names: dict[str, str]) -> frozenset[str]:
    """The :data:`AMBIGUOUS_DB` verbs this file's db imports license.

    Shared by the JVM dialects in place of "any db-kind import".
    """
    evidence = _db_import_evidence(io_names)
    if evidence == "library":
        return AMBIGUOUS_DB
    return _JDBC_AMBIGUOUS_DB if evidence == "jdbc" else frozenset()


def is_repository_call(method: str, root: str, io_names: dict[str, str]) -> bool:
    """``repo.findByName(...)`` / ``repo.findAll()``: a repository query, not an in-memory call.

    Needs a derived-query name or a shared repository verb, plus a receiver
    named like a repository or DAO, or a db library (Spring Data, JPA,
    Hibernate, ...) imported in the file.
    """
    if not (_SPRING_DERIVED.match(method) or method in _SHARED_REPO_METHODS):
        return False
    return bool(_REPOSITORY_RECEIVER.search(root)) or _db_import_evidence(io_names) == "library"


def _receiver_root(receiver: str) -> str:
    """``a.b.c`` -> ``a``; ``this.repo.x`` -> ``repo`` (``this`` names no import or receiver)."""
    parts = receiver.split(".")
    return parts[1] if parts[0] == "this" and len(parts) > 1 else parts[0]


class JavaPerfDialect(BasePerfDialect):
    language = "java"
    lock_acquire_functions = JAVA_LOCK_ACQUIRE_FUNCTIONS
    spin_loop_header = JAVA_SPIN_LOOP_HEADER
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            # Centrality-gated / nesting-confidence markers + the
            # block-scoped lock→I/O case (``synchronized`` is a held region).
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            "blocking_io_under_lock",
        }
    )

    def is_lock_scope(self, node: Node) -> bool:
        # ``synchronized (x) { ... }`` — the body is the held-lock region.
        return node.type == "synchronized_statement"

    # ``s += "x"`` is an ``assignment_expression`` with the literal directly on
    # the ``right`` field (no list wrapper, unlike Go).
    string_literal_kinds = frozenset({"string_literal"})
    aug_assign_kinds = frozenset({"assignment_expression"})

    # -- Java callee arm ------------------------------------------------------

    def callee_method_name(self, call_node: Node) -> str | None:
        if call_node.type == "object_creation_expression":
            t = call_node.child_by_field_name("type")
            if t is not None and t.text:
                return t.text.decode("utf-8", "replace").split(".")[-1]
            return None
        name = call_node.child_by_field_name("name")
        if name is not None and name.text:
            return name.text.decode("utf-8", "replace")
        return None

    def callee_root_name(self, call_node: Node) -> str | None:
        if call_node.type == "object_creation_expression":
            t = call_node.child_by_field_name("type")
            if t is not None and t.text:
                return t.text.decode("utf-8", "replace").split(".")[-1]
            return None
        obj = call_node.child_by_field_name("object")
        if obj is not None and obj.text:
            return _receiver_root(obj.text.decode("utf-8", "replace"))
        # A bare ``foo()`` (no receiver) — the name is the root.
        name = call_node.child_by_field_name("name")
        if name is not None and name.text:
            return name.text.decode("utf-8", "replace")
        return None

    def callee_is_attribute(self, call_node: Node) -> bool:
        if call_node.type == "object_creation_expression":
            return True  # a constructor sink (``new FileInputStream``)
        # A ``method_invocation`` with a receiver (``repo.find()``) is
        # attribute-style; a bare ``find()`` is not.
        return call_node.child_by_field_name("object") is not None

    # -- lexicon --------------------------------------------------------------

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
        # ``has_db_import`` is "any db-kind import", which counts a type-only
        # ``java.sql.JDBCType``; :func:`ambiguous_db_verbs` reads the same
        # ``io_names`` and tells the import kinds apart.
        root_kind = io_names.get(root)
        net_ev = root_kind == "network" or "network" in io_names.values()

        if method in FS_CONSTRUCTORS:
            return "filesystem"
        if method in NET_CONSTRUCTORS:
            return "network"
        if is_attribute and method in RECEIVER_DB_METHODS:
            return "db"
        if is_repository_call(method, root, io_names):
            return "db"
        if method in REST_TEMPLATE_METHODS:
            return "network"
        if method in WEBCLIENT_BLOCK and net_ev:
            return "network"
        if method == "send" and net_ev:
            return "network"
        if root == "Files" and method in FILES_METHODS:
            return "filesystem"
        if method == "exec":  # Runtime.getRuntime().exec(...)
            return "subprocess"
        if is_attribute and method in AMBIGUOUS_DB:
            return "db" if method in ambiguous_db_verbs(io_names) else None
        return None

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``new RestTemplate()`` etc. — an ``object_creation_expression`` whose
        # extracted method is the type name.
        if node.type == "object_creation_expression":
            return "resource_construction_in_loop" if method in JAVA_RESOURCE_CTORS else None
        if method in JAVA_RESOURCE_METHODS:
            return "resource_construction_in_loop"
        # ``lock.lock()`` — a method on a receiver (not a bare ``lock()``).
        if method in JAVA_LOCK_METHODS and node.child_by_field_name("object") is not None:
            return "lock_in_loop"
        # ``Pattern.compile(...)`` recompiled per iteration (no cached Pattern).
        # ``root`` is the first segment, so an FQN ``java.util.regex.Pattern``
        # lands as ``java``; match on the receiver's last segment instead.
        if method != "compile":
            return None
        obj = node.child_by_field_name("object")
        if obj is not None and obj.text:
            return (
                "regex_compile_in_loop"
                if obj.text.decode("utf-8", "replace").split(".")[-1] == "Pattern"
                else None
            )
        return "regex_compile_in_loop" if root == "Pattern" else None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``synchronized (x) { ... }`` taken every iteration is a contention
        # site, the block-statement counterpart of ``lock.lock()``.
        if node.type == "synchronized_statement":
            return "lock_in_loop"
        return None

    def is_chunked_loop(self, node: Node) -> bool:
        """``for (int i = 0; i < n; i += step)``, or ``for (var c : Lists.partition(xs, n))``."""
        if node.type == "for_statement":
            return self._steps_by_chunk(node.child_by_field_name("update"))
        if node.type == "enhanced_for_statement":
            value = node.child_by_field_name("value")
            if value is None or value.type != "method_invocation":
                return False
            name = value.child_by_field_name("name")
            if name is None or name.text is None:
                return False
            return name.text.decode("utf-8", "replace") in _PARTITION_CALLS
        return False


DIALECT = JavaPerfDialect()
