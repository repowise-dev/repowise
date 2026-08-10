"""Kotlin ``PerfDialect``.

Rides the JVM lexicon: Kotlin call sites hit ``java.*`` / Spring / JDBC interop
verbatim, so the Java dialect's sink tables are imported as the base (the same
posture Scala takes) and extended with the Kotlin-native boundaries —
``kotlin.io``'s ``File`` extension functions, JetBrains Exposed, and Ktor's
client verbs.

Flagship: a Spring-Data / Exposed query inside ``ids.forEach { … }``. Idiomatic
Kotlin iterates through *combinators taking a trailing lambda*, not ``for``
nodes, so this dialect implements the shared :meth:`block_loop_body` hook Ruby
introduced rather than re-deriving the question — a ``call_expression`` whose
method is a known full-iteration combinator AND that carries an
``annotated_lambda`` is a loop whose body is that lambda.

Coroutines: ``suspend`` is a **modifier token text** (``modifiers`` ->
``function_modifier``), not a node type, so ``async_function_kinds`` stays empty
and :meth:`is_async_fn` sniffs the modifier — the case
``BasePerfDialect.is_async_fn``'s docstring names. Inside a ``suspend fun``,
``runBlocking`` and ``Thread.sleep`` stall the dispatcher thread.

Grammar seams (verified against the installed tree-sitter-kotlin, not assumed):

* a ``call_expression`` has **no ``function`` field** — the callee is its first
  named child (an ``identifier`` for a bare call, a ``navigation_expression``
  for ``a.b()``), so all three callee hooks are overridden;
* a ``navigation_expression`` labels **no** ``object`` / ``field`` field either;
  receiver and member are positional (first / last named child);
* ``for_statement`` / ``while_statement`` label **no ``body`` field** — the
  block is an unlabeled child — so :meth:`loop_body` supplies it, which is what
  keeps ``for (u in repo.findAll())`` from reading as a sink *inside* the loop;
* ``x = …`` and ``x += …`` are both an ``assignment`` node told apart by the
  ``operator`` field (as in Java / Go), so the base ``+=`` predicate applies.

Kotlin has no ``new``: ``OkHttpClient()`` is an ordinary ``call_expression``
over a bare ``identifier``, so constructor sinks are matched by *type name*
against the same deliberately-small Java table (a bare ``HttpClient`` stays
excluded — it collides with user wrappers).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import BasePerfDialect
from .java import (
    _SPRING_DERIVED,
    AMBIGUOUS_DB,
    FILES_METHODS,
    FS_CONSTRUCTORS,
    JAVA_LOCK_METHODS,
    JAVA_RESOURCE_CTORS,
    JAVA_RESOURCE_METHODS,
    JDBC_METHODS,
    JPA_METHODS,
    NET_CONSTRUCTORS,
    REST_TEMPLATE_METHODS,
    SPRING_REPO_METHODS,
)

if TYPE_CHECKING:
    from tree_sitter import Node

# Full-iteration combinators (the shared block-iteration definition — see
# ``BasePerfDialect.block_loop_body``). Early-exit searches (``find`` /
# ``firstOrNull`` / ``any`` / ``all`` / ``none``) are deliberately absent: they
# may stop after one element, so calling their lambda a per-iteration body would
# overclaim. ``let`` / ``apply`` / ``also`` / ``run`` / ``with`` are scope
# functions that run their lambda EXACTLY ONCE and must never be loops.
ITERATION_LAMBDA_METHODS: frozenset[str] = frozenset(
    {
        "forEach",
        "forEachIndexed",
        "onEach",
        "onEachIndexed",
        "map",
        "mapNotNull",
        "mapIndexed",
        "mapIndexedNotNull",
        "flatMap",
        "associate",
        "associateBy",
        "associateWith",
        "filter",
        "filterNot",
        "filterIndexed",
        "filterIsInstance",
        "partition",
        "groupBy",
        "sortedBy",
        "sortedByDescending",
        "sumOf",
        "maxByOrNull",
        "minByOrNull",
        "count",
        "fold",
        "reduce",
        "repeat",
    }
)
# ``repeat(n) { … }`` iterates a count, not a collection.
_COUNTING_LAMBDA_METHODS: frozenset[str] = frozenset({"repeat"})

# ``kotlin.io`` ``File`` extension functions — restricted to the names that
# ONLY a ``File`` carries.
#
# The generic stream verbs (``readText`` / ``writeText`` / ``readBytes`` /
# ``writeBytes`` / ``copyTo`` / ``bufferedReader`` / ``bufferedWriter``) were
# tried here and removed: ``kotlinx-io`` and Ktor deliberately mirror those
# names on IN-MEMORY buffers (``frame.readText()`` on a WebSocket frame,
# ``packet.copyTo(channel)``), and no static evidence separates the two
# receivers. They produced ~20 false positives across ktor — by far this
# dialect's largest FP class — against a handful of genuine ``File`` reads.
# The names below have no buffer homonym: they are file-tree or line-oriented
# operations that only exist on ``java.io.File``.
KOTLIN_IO_FS_METHODS: frozenset[str] = frozenset(
    {
        "appendText",
        "appendBytes",
        "readLines",
        "forEachLine",
        "useLines",
        "printWriter",
        "copyRecursively",
        "deleteRecursively",
        "walkTopDown",
        "walkBottomUp",
    }
)
# JetBrains Exposed — the distinctive stratum only. ``select`` / ``insert`` /
# ``update`` / ``delete`` collide head-on with collection and builder verbs and
# are left to the evidence-gated ``AMBIGUOUS_DB`` stratum.
EXPOSED_DB_METHODS: frozenset[str] = frozenset(
    {
        "selectAll",
        "deleteWhere",
        "deleteAll",
        "insertAndGetId",
        "insertIgnore",
        "batchInsert",
        "upsert",
        "updateReturning",
    }
)
# Ktor client round-trip finishers. The generic HTTP verbs (``get`` / ``post``
# / ``put`` / ``delete``) are deliberately NOT here: on the JVM they are also
# the *route-registration* DSL of every Kotlin web framework
# (``app.routes.get("/x") { … }`` in Javalin, ``get("/x") { … }`` in Ktor
# server), and a member call cannot be told from a client round-trip
# statically. Smoke-testing javalin produced 3/3 false positives from exactly
# that collision, so the verb stratum was dropped: a Ktor client call is caught
# only through the finishers below. Recall ceiling, not a precision one.
KOTLIN_NET_METHODS: frozenset[str] = frozenset({"bodyAsText", "bodyAsChannel", "submitForm"})

# ``Regex("…")`` / ``"…".toRegex()`` / ``Pattern.compile("…")`` — a fresh
# compiled pattern per iteration instead of a hoisted ``val``.
_REGEX_CTOR_NAMES: frozenset[str] = frozenset({"Regex", "Pattern"})

# Executor-blocking calls inside a ``suspend fun``. ``runBlocking`` re-enters a
# blocking event loop on a coroutine dispatcher thread (the canonical Kotlin
# deadlock shape); ``Thread.sleep`` stalls it (use ``delay``). ``.get()`` on a
# future is deliberately absent — ``get`` is the Map/List accessor and would
# false-fire on every ``map.get(k)``.
_BLOCKING_BARE: frozenset[str] = frozenset({"runBlocking"})

# A Kotlin string template placeholder: an unescaped ``$`` introducing either
# ``{expr}`` or a bare identifier. Distinguishes an interpolated pattern (not
# hoistable) from a constant one, including a trailing regex ``$`` anchor.
_TEMPLATE_RE = re.compile(r"(?<!\\)\$[A-Za-z_{]")

# The ambiguous-DB stratum, narrowed for Kotlin. ``find`` / ``get`` / ``count``
# are removed from the shared Java set because they are *stdlib collection*
# combinators here (``Iterable.find`` / ``List.get`` / ``Iterable.count``) with
# no Java equivalent — smoke-testing Exposed, ``StatementType.entries.find { }``
# (an in-memory enum lookup in a JDBC-importing file) was the only false
# positive this stratum produced. ``execute`` / ``save`` have no collection
# homonym and stay.
KOTLIN_AMBIGUOUS_DB: frozenset[str] = AMBIGUOUS_DB - frozenset({"find", "get", "count"})

_STRING_KINDS: frozenset[str] = frozenset({"string_literal"})
# Mirrors ``_KOTLIN.loop_kinds``; declared here so the reset-per-iteration walk
# needs no lmap threading (the same posture the dataflow dialects take).
_LOOP_KINDS: frozenset[str] = frozenset({"for_statement", "while_statement", "do_while_statement"})
# Held-lock regions: ``synchronized(x) { … }`` is a bare stdlib call,
# ``lock.withLock { … }`` the java.util.concurrent extension.
_LOCK_SCOPE_METHODS: frozenset[str] = frozenset({"synchronized", "withLock"})


def _decode(node: Node | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8", "replace")


def _callee(call_node: Node) -> Node | None:
    """The callee of a ``call_expression`` — its first named child (the grammar
    labels no ``function`` field)."""
    return next((c for c in call_node.children if c.is_named), None)


class KotlinPerfDialect(BasePerfDialect):
    language = "kotlin"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            "blocking_sync_in_async",
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            "blocking_io_under_lock",
        }
    )

    string_literal_kinds = _STRING_KINDS
    aug_assign_kinds = frozenset({"assignment"})

    # -- callee extraction (no ``function`` field; positional navigation) ------

    def callee_method_name(self, call_node: Node) -> str | None:
        fn = _callee(call_node)
        if fn is None:
            return None
        if fn.type == "navigation_expression":
            # The member is the LAST named child (the grammar labels no field);
            # scan backwards so the common two-child case stops immediately.
            for c in reversed(fn.children):
                if c.is_named:
                    return _decode(c)
            return None
        if fn.type == "identifier":
            return _decode(fn)
        return None

    def callee_root_name(self, call_node: Node) -> str | None:
        node = _callee(call_node)
        # Walk to the bottom of a ``a.b().c()`` chain — the leftmost receiver is
        # the lexicon key (``Files`` / ``Pattern`` / a Spring repository name).
        # ``next(...)`` rather than a list comprehension: this runs on every call
        # node in the file, and only the first named child is ever needed.
        for _ in range(8):
            if node is None or node.type == "identifier":
                break
            node = next((c for c in node.children if c.is_named), None)
        return _decode(node)

    def callee_is_attribute(self, call_node: Node) -> bool:
        fn = _callee(call_node)
        return fn is not None and fn.type == "navigation_expression"

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

        # JVM interop — verbatim from the Java lexicon.
        if method in FS_CONSTRUCTORS:
            return "filesystem"
        if method in NET_CONSTRUCTORS:
            return "network"
        if method in JDBC_METHODS or method in JPA_METHODS or method in SPRING_REPO_METHODS:
            return "db"
        # A Spring-Data derived query is called on a repository INSTANCE, so the
        # receiver is lower-cased. Requiring that excludes the static-factory
        # collisions the bare ``…By[A-Z]`` pattern otherwise picks up —
        # ``InetAddress.getByName(host)`` matched it in ktor.
        if _SPRING_DERIVED.match(method) and is_attribute and root[:1].islower():
            return "db"
        if method in REST_TEMPLATE_METHODS:
            return "network"
        if root == "Files" and method in FILES_METHODS:
            return "filesystem"
        if root == "Runtime" and method == "exec":
            return "subprocess"

        # Kotlin-native boundaries.
        if is_attribute and method in KOTLIN_IO_FS_METHODS:
            return "filesystem"
        if is_attribute and method in EXPOSED_DB_METHODS:
            return "db"
        if is_attribute and net_ev and method in KOTLIN_NET_METHODS:
            return "network"
        if is_attribute and db_ev and method in KOTLIN_AMBIGUOUS_DB:
            return "db"
        return None

    # -- loops ----------------------------------------------------------------

    def loop_body(self, node: Node) -> Node | None:
        """The ``block`` of a ``for`` / ``while`` / ``do-while``.

        tree-sitter-kotlin labels no ``body`` field, so without this the loop
        HEADER would count as per-iteration and ``for (u in repo.findAll())``
        would read as an ``io_in_loop``. A braceless single-statement body
        (``for (x in xs) f(x)``) has no ``block`` node; returning ``None`` there
        keeps the walker's conservative every-child fallback, which is correct
        for that shape (the body is a sibling of the header, and a header sink
        in a braceless loop is a recall-only miss).
        """
        return next((c for c in node.children if c.type == "block"), None)

    @staticmethod
    def _for_iterable(node: Node) -> Node | None:
        """The iterated expression of ``for (x in <iterable>) …`` — the named
        child after the binder ``variable_declaration``."""
        named = [c for c in node.children if c.is_named]
        return named[1] if len(named) >= 2 else None

    def block_loop_body(self, node: Node) -> Node | None:
        if node.type != "call_expression":
            return None
        lam = next((c for c in node.children if c.type == "annotated_lambda"), None)
        if lam is None:
            return None
        return lam if self.callee_method_name(node) in ITERATION_LAMBDA_METHODS else None

    def is_constant_loop(self, node: Node) -> bool:
        """``for (i in 0..9)`` / ``for (i in 0 until 8)`` — a literal range is a
        compile-time bound, not a data-dependent multiplier. ``repeat(3) { … }``
        is the combinator spelling of the same thing."""
        if node.type == "for_statement":
            it = self._for_iterable(node)
            if it is None:
                return False
            if it.type == "range_expression":
                return all(c.type == "number_literal" for c in it.children if c.is_named)
            # ``0 until 8`` / ``8 downTo 0`` are INFIX CALLS, not range nodes:
            # an ``infix_expression`` whose three named children are
            # ``<literal> <identifier> <literal>`` (the operator is an ordinary
            # named identifier, not a token — verified against the grammar).
            if it.type == "infix_expression":
                named = [c for c in it.children if c.is_named]
                return (
                    len(named) == 3
                    and named[1].type == "identifier"
                    and named[1].text in (b"until", b"downTo")
                    and named[0].type == "number_literal"
                    and named[2].type == "number_literal"
                )
            return False
        if node.type == "call_expression" and self.callee_method_name(node) == "repeat":
            args = next((c for c in node.children if c.type == "value_arguments"), None)
            if args is None:
                return False
            first = next((c for c in args.children if c.is_named), None)
            inner = next((c for c in first.children if c.is_named), None) if first else None
            return inner is not None and inner.type == "number_literal"
        return False

    def is_iteration_loop(self, node: Node) -> bool:
        # ``while`` / ``do-while`` spin a cursor (pagination / retry); ``for``
        # and the collection combinators multiply over data.
        if node.type == "for_statement":
            return True
        if node.type == "call_expression":
            return self.callee_method_name(node) not in _COUNTING_LAMBDA_METHODS
        return False

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type == "for_statement":
            return self._dotted_path(self._for_iterable(node))
        if node.type == "call_expression":
            if self.callee_method_name(node) in _COUNTING_LAMBDA_METHODS:
                return None
            fn = _callee(node)
            if fn is None or fn.type != "navigation_expression":
                return None
            named = [c for c in fn.children if c.is_named]
            return self._dotted_path(named[0]) if named else None
        return None

    # -- string concat --------------------------------------------------------

    def is_string_concat(self, node: Node) -> bool:
        """``acc += "<lit>"`` where ``acc`` is a same-file ``var`` initialised to
        a string literal.

        The var-binding gate is load-bearing, exactly as in Scala: Kotlin's
        ``+=`` also appends to a mutable collection (``items += "x"`` on a
        ``MutableList<String>`` is amortized O(1)) and ``sb += "x"`` on a
        ``StringBuilder`` is ``append``. Requiring a provable
        ``var <name> = "<string>"`` binding keeps those from firing.
        ``StringBuilder.append(…)`` is a call, so it can never reach here.
        """
        if not super().is_string_concat(node):
            return False
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier" or left.text is None:
            return False  # opaque / member target — not provably a String
        if not self._is_string_var(node, left.text):
            return False
        # ``var s = "<…>"`` declared INSIDE the loop is reset every pass, so the
        # accumulation is bounded per iteration (the ``continue`` HTML-token
        # builder — 3/3 of this marker's smoke findings were this shape).
        return not self.resets_per_iteration(node, left.text, _LOOP_KINDS)

    def binds_name(self, node: Node, name: bytes) -> bool:
        if node.type == "property_declaration":
            binder = next((c for c in node.children if c.is_named), None)
            return (
                binder is not None and binder.type == "variable_declaration" and binder.text == name
            )
        if node.type == "assignment":
            op = node.child_by_field_name("operator")
            left = node.child_by_field_name("left")
            return op is not None and op.text == b"=" and left is not None and left.text == name
        return False

    @staticmethod
    def _is_string_var(node: Node, name: bytes) -> bool:
        """True when the file carries ``var <name> = "<string literal>"``.

        Only ``var`` counts: a ``val`` cannot be the target of ``+=`` at all
        (the compiler rejects it), so a ``val`` match would mean the name was
        rebound and the binding we found is a different one.
        """
        root = node
        while root.parent is not None:
            root = root.parent
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur.type == "property_declaration" and any(c.type == "var" for c in cur.children):
                named = [c for c in cur.children if c.is_named]
                binder = named[0] if named else None
                value = named[1] if len(named) >= 2 else None
                if (
                    binder is not None
                    and binder.type == "variable_declaration"
                    and binder.text == name
                    and value is not None
                    and value.type in _STRING_KINDS
                ):
                    return True
            stack.extend(cur.children)
        return False

    # -- coroutines -----------------------------------------------------------

    def is_async_fn(self, node: Node) -> bool:
        """``suspend fun`` — the marker is a ``function_modifier`` token TEXT
        inside a ``modifiers`` child, not a node type."""
        mods = next((c for c in node.children if c.type == "modifiers"), None)
        if mods is None:
            return False
        return any(c.text == b"suspend" for c in mods.children)

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        if method in _BLOCKING_BARE:
            return method
        if root == "Thread" and method == "sleep":
            return "Thread.sleep"
        return None

    # -- extra loop markers ---------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``Regex("^…$")`` / ``Pattern.compile("^…$")`` / ``"^…$".toRegex()``
        # recompiled per iteration. Only a literal pattern is unambiguously
        # hoistable (a dynamic one may legitimately vary) — the same gate Rust,
        # Go and Java use.
        if (method in _REGEX_CTOR_NAMES and root in _REGEX_CTOR_NAMES) or method == "compile":
            if root in _REGEX_CTOR_NAMES and self._has_literal_first_arg(node):
                return "regex_compile_in_loop"
            return None
        if method == "toRegex":
            fn = _callee(node)
            recv = (
                next((c for c in fn.children if c.is_named), None)
                if fn is not None and fn.type == "navigation_expression"
                else None
            )
            return (
                "regex_compile_in_loop" if recv is not None and recv.type in _STRING_KINDS else None
            )
        # Heavy clients to hoist, not rebuild per iteration. Kotlin has no
        # ``new``, so a constructor is a bare call whose method IS the type name.
        if method in JAVA_RESOURCE_CTORS and not self.callee_is_attribute(node):
            return "resource_construction_in_loop"
        if method in JAVA_RESOURCE_METHODS:
            return "resource_construction_in_loop"
        # ``lock.lock()`` / ``lock.withLock { … }`` / ``synchronized(x) { … }``
        # taken every iteration is a contention site.
        if method in JAVA_LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        if method in _LOCK_SCOPE_METHODS:
            return "lock_in_loop"
        return None

    @staticmethod
    def _has_literal_first_arg(node: Node) -> bool:
        """True when the call's first argument is a *constant* string literal.

        A Kotlin string template (``Regex("\\\\b$name\\\\b")``) is a
        ``string_literal`` too, but its value changes every iteration, so it is
        not hoistable and must not fire — Exposed's ``DescriptionGenerator``
        builds exactly that shape inside a ``.map``. The grammar only emits an
        ``interpolation`` node for the ``${…}`` form (a bare ``$name`` splits
        into plain ``string_content``), so the text is tested instead, which
        also keeps a trailing regex anchor (``"^literal$"``) constant.
        """
        args = next((c for c in node.children if c.type == "value_arguments"), None)
        if args is None:
            return False
        first = next((c for c in args.children if c.is_named), None)
        if first is None:
            return False
        inner = next((c for c in first.children if c.is_named), None)
        if inner is None or inner.type not in _STRING_KINDS or inner.text is None:
            return False
        return not _TEMPLATE_RE.search(inner.text.decode("utf-8", "replace"))

    def is_lock_scope(self, node: Node) -> bool:
        # ``synchronized(x) { … }`` / ``lock.withLock { … }`` — the trailing
        # lambda is the held region (the walker raises lock_depth for block-
        # typed children only, which excludes the lock-object argument).
        if node.type != "call_expression":
            return False
        return self.callee_method_name(node) in _LOCK_SCOPE_METHODS


DIALECT = KotlinPerfDialect()
