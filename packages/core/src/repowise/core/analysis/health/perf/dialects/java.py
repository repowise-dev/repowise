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
# ``countByOwner`` … — an extremely distinctive signature, treated as db without
# an import gate.
_SPRING_DERIVED = re.compile(r"^(find|get|query|count|exists|stream|read|delete)By[A-Z]")

# JDBC — unambiguous cursor/statement round-trips.
JDBC_METHODS: frozenset[str] = frozenset(
    {"executeQuery", "executeUpdate", "executeBatch", "executeLargeUpdate", "prepareStatement"}
)
# JPA / Hibernate EntityManager + Query.
JPA_METHODS: frozenset[str] = frozenset(
    {"getResultList", "getSingleResult", "getResultStream", "createQuery", "createNativeQuery"}
)
# Spring-Data CrudRepository finishers that are distinctive enough on their own.
SPRING_REPO_METHODS: frozenset[str] = frozenset(
    {"saveAll", "findAll", "findAllById", "deleteAll", "deleteAllById"}
)
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


class JavaPerfDialect(BasePerfDialect):
    language = "java"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            # Phase 7b — centrality-gated / nesting-confidence markers + the
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
            return obj.text.decode("utf-8", "replace").split(".")[0]
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
        root_kind = io_names.get(root)
        db_ev = has_db_import or root_kind == "db"
        net_ev = root_kind == "network" or "network" in io_names.values()

        if method in FS_CONSTRUCTORS:
            return "filesystem"
        if method in NET_CONSTRUCTORS:
            return "network"
        if method in JDBC_METHODS or method in JPA_METHODS or method in SPRING_REPO_METHODS:
            return "db"
        if _SPRING_DERIVED.match(method):
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
        if is_attribute and db_ev and method in AMBIGUOUS_DB:
            return "db"
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


DIALECT = JavaPerfDialect()
