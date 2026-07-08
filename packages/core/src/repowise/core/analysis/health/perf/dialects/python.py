"""Python ``PerfDialect``.

Extracted verbatim from the original ``_py_sink_kind`` (``io_boundaries.py``)
and the Python branches of the walker (``_blocking_sync_api`` / ``_is_constant_for``
/ the ``_PY_STRING_KINDS`` string-concat predicate). The 6a refactor changes
zero Python behavior — the defect golden + perf suite lock that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# Network round-trip verbs (shared across languages that name HTTP methods).
HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "send", "stream"}
)

# Python DBAPI / SQLAlchemy execution sinks, split into three strata so the
# ambiguous accessors can be gated harder than the unambiguous ones (see
# ``sink_kind``).
PY_DB_UNAMBIGUOUS: frozenset[str] = frozenset(
    {
        "execute",
        "executemany",
        "scalars",
        "scalar",
        "scalar_one",
        "scalar_one_or_none",
        "fetchone",
        "fetchall",
        "fetchmany",
    }
)
PY_DB_COMMIT: frozenset[str] = frozenset({"commit"})
PY_DB_AMBIGUOUS: frozenset[str] = frozenset({"all", "first", "one", "one_or_none"})
PY_SUBPROC_METHODS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)

# Python string-literal node kinds (f-strings parse as ``string`` too).
_PY_STRING_KINDS: frozenset[str] = frozenset({"string", "concatenated_string"})
_PY_AUG_ASSIGN_KINDS: frozenset[str] = frozenset({"augmented_assignment"})

# Heavy-resource constructors: building one of these per loop iteration opens a
# fresh connection / client / pool instead of reusing a hoisted one. Keyed as
# ``(root, method)`` pairs whose root is a distinctive I/O library (so the match
# is unambiguous without import resolution) ...
_PY_RESOURCE_CTORS: frozenset[tuple[str, str]] = frozenset(
    {
        ("sqlite3", "connect"),
        ("psycopg", "connect"),
        ("psycopg2", "connect"),
        ("pymysql", "connect"),
        ("MySQLdb", "connect"),
        ("aiosqlite", "connect"),
        ("httpx", "Client"),
        ("httpx", "AsyncClient"),
        ("requests", "Session"),
        ("aiohttp", "ClientSession"),
        ("boto3", "client"),
        ("boto3", "resource"),
        ("redis", "Redis"),
        ("redis", "StrictRedis"),
        ("pymongo", "MongoClient"),
    }
)
# ... plus a few constructors distinctive enough on their own (the rightmost
# name), so an aliased ``from sqlalchemy import create_engine`` still resolves.
_PY_RESOURCE_METHODS: frozenset[str] = frozenset({"create_engine", "MongoClient"})

# Lock acquisition (the contention side only — never ``release``, which would
# double-count the same critical section). ``.acquire()`` is the threading /
# asyncio / multiprocessing / filelock primitive verb.
_PY_LOCK_METHODS: frozenset[str] = frozenset({"acquire"})

# RHS node kinds that prove a name is bound to a list (the membership gate).
_PY_LIST_RHS_KINDS: frozenset[str] = frozenset({"list", "list_comprehension"})
# Builtins whose call result is provably a list.
_PY_LIST_BUILTINS: frozenset[str] = frozenset({"list", "sorted"})
# RHS shapes that make a name a NON-list container (a set/dict literal or
# comprehension, or a ``set()`` / ``dict()`` / ``frozenset()`` call). A name
# bound to one of these ANYWHERE in the file is ambiguous and excluded from the
# membership gate, even if another scope binds the same name to a list — the
# ``seen = []`` in one function / ``seen: set = set()`` in another collision.
_PY_NONLIST_RHS_KINDS: frozenset[str] = frozenset(
    {"set", "dictionary", "set_comprehension", "dictionary_comprehension"}
)
_PY_NONLIST_BUILTINS: frozenset[str] = frozenset({"set", "dict", "frozenset"})


class PythonPerfDialect(BasePerfDialect):
    language = "python"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "blocking_sync_in_async",
            "resource_construction_in_loop",
            "lock_in_loop",
            "serial_await_in_loop",
            "membership_test_against_list_in_loop",
            # Phase 7b — centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # Phase 7d — Python-specific quadratic anti-patterns.
            "list_insert_zero_in_loop",
            "pd_concat_in_loop",
            # Header-call marker: the slow iterable IS the loop driver.
            "pandas_iterrows_in_loop",
        }
    )

    string_literal_kinds = _PY_STRING_KINDS
    aug_assign_kinds = _PY_AUG_ASSIGN_KINDS

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
        db_evidence = has_db_import or root_kind == "db"

        # ``asyncio.sleep`` / ``time.sleep`` / ``trio.sleep`` is a cooperative
        # yield, never an I/O round-trip — but ``await asyncio.sleep(...)`` would
        # otherwise hit the awaited-network arm below when ``asyncio`` is
        # import-classified as network, FP-ing ``io_in_loop`` / ``serial_await``
        # on every backoff/poll loop (Phase-7c headroom corpus). No legitimate
        # execution sink is named ``sleep``, so excluding it costs no recall.
        if method == "sleep":
            return None

        if method in PY_DB_UNAMBIGUOUS:
            # A real DB sink is always a method on a session/cursor/result
            # object; a bare identifier call (the builtin ``all(...)``) is not.
            return "db" if is_attribute else None
        if method in PY_DB_COMMIT:
            # ``.commit`` is a DB verb but GitPython exposes ``repo.commit()``.
            # Require db evidence in the file / on the receiver.
            return "db" if (is_attribute and db_evidence) else None
        if method in PY_DB_AMBIGUOUS:
            # ``.all`` / ``.first`` / ``.one`` collide with ordinary collection
            # helpers — the riskiest stratum. Gate on db evidence.
            return "db" if (is_attribute and db_evidence) else None
        if root == "subprocess" and method in PY_SUBPROC_METHODS:
            return "subprocess"
        if method == "Popen":
            return "subprocess"
        if root == "open" and method == "open":  # bare ``open(...)`` builtin
            return "filesystem"
        if method == "urlopen":
            return "network"
        if root_kind == "network" and method in HTTP_VERBS:
            return "network"
        if awaited and root_kind == "network":
            return "network"
        return None

    def is_constant_loop(self, node: Node) -> bool:
        """True if a Python for-loop iterates a compile-time-constant bound.

        Catches ``for _ in range(<int literals>)``, ``for x in (<literal>)``,
        and ``for x in ALL_CAPS`` (a named module constant by convention).
        ``while`` loops are never constant.
        """
        if node.type != "for_statement":
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type in ("list", "tuple", "set"):
            return True
        # A bare ALL_CAPS identifier is a named constant by convention.
        if right.type == "identifier" and right.text is not None:
            name = right.text.decode("utf-8", "replace")
            if name.isupper() and len(name) > 1:
                return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is None or (fn.text or b"").decode("utf-8", "replace") != "range":
                return False
            args = right.child_by_field_name("arguments")
            if args is None:
                return False
            for a in args.children:
                if not a.is_named:
                    continue
                if a.type == "integer":
                    continue
                if a.type == "unary_operator" and any(c.type == "integer" for c in a.children):
                    continue
                return False  # a non-literal arg (e.g. len(x)) ⇒ data-dependent
            return True
        return False

    # Builtins that wrap a collection without changing what is iterated, so the
    # same-collection O(n^2) shape sees through them: ``for x in enumerate(items)``
    # iterates ``items``.
    _ITER_WRAPPERS: frozenset[str] = frozenset({"enumerate", "sorted", "reversed", "list", "set"})

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type != "for_statement":
            return None
        right = node.child_by_field_name("right")
        if right is None:
            return None
        if right.type in ("identifier", "attribute"):
            return self._dotted_path(right)
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is None or fn.text is None:
                return None
            if fn.text.decode("utf-8", "replace") not in self._ITER_WRAPPERS:
                return None
            args = right.child_by_field_name("arguments")
            if args is None:
                return None
            first = next((c for c in args.children if c.is_named), None)
            if first is not None and first.type == "identifier" and first.text is not None:
                return first.text.decode("utf-8", "replace")
        return None

    def is_string_concat(self, node: Node) -> bool:
        """``s += "x"`` accumulation — but skip an accumulator that is *reset*
        each iteration of an enclosing loop.

        ``buf = base[:N]; ... buf += part`` inside a loop builds a fresh, bounded
        string per iteration (not the O(n^2) cross-iteration accumulation the
        marker targets), so it is a false positive. Phase-7c headroom corpus:
        the reset-per-iteration shape was the dominant FP class (Py 77.8%). When
        the same name is plainly re-assigned inside an enclosing loop body, the
        ``+=`` cannot accumulate across iterations, so do not flag it.
        """
        if not super().is_string_concat(node):
            return False
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier" or left.text is None:
            return True  # opaque target -> keep the (precision-first) flag
        name = left.text.decode("utf-8", "replace")
        cur = node.parent
        while cur is not None:
            if cur.type in ("for_statement", "while_statement"):
                body = cur.child_by_field_name("body")
                if body is not None and self._resets_name(body, name, node):
                    return False
            cur = cur.parent
        return True

    @staticmethod
    def _resets_name(body: Node, name: str, exclude: Node) -> bool:
        """True if *body* contains a plain ``name = ...`` assignment (not the
        ``+=`` node *exclude* itself) — i.e. the accumulator is reset here."""
        stack: list[Node] = [body]
        while stack:
            n = stack.pop()
            if n.type == "assignment" and not (
                n.start_byte == exclude.start_byte and n.end_byte == exclude.end_byte
            ):
                lhs = n.child_by_field_name("left")
                if (
                    lhs is not None
                    and lhs.type == "identifier"
                    and lhs.text is not None
                    and lhs.text.decode("utf-8", "replace") == name
                ):
                    return True
            stack.extend(n.children)
        return False

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        """The offending API name if ``root.method`` is a known blocking sync call.

        A small, high-precision allowlist (mirrors ruff ASYNC210/230/251):
        always-synchronous stdlib / ``requests`` calls that block the event
        loop when run inside an ``async def``.
        """
        if root == "time" and method == "sleep":
            return "time.sleep"
        if root == "requests" and method in HTTP_VERBS:
            return f"requests.{method}"
        if root == "subprocess" and method in PY_SUBPROC_METHODS:
            return f"subprocess.{method}"
        if root == "os" and method == "system":
            return "os.system"
        if root == "open" and method == "open":
            return "open"
        return None

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        if (root, method) in _PY_RESOURCE_CTORS or method in _PY_RESOURCE_METHODS:
            return "resource_construction_in_loop"
        # ``lock.acquire()`` — a method call on a receiver (never the builtin
        # ``acquire(...)``, which does not exist; the attribute gate is a cheap
        # extra guard against a bare-name collision).
        if method in _PY_LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        # ``lst.insert(0, x)`` each iteration shifts the whole list -> O(n^2);
        # ``collections.deque.appendleft`` / build-then-reverse is O(n). Gated to
        # a literal ``0`` first arg (``insert(i, x)`` at a variable index is not
        # the front-insertion anti-pattern) AND to a list that is not re-created
        # each iteration (a fresh ``buf = [...]; buf.insert(0, x)`` is bounded,
        # not O(n^2) — the same reset-per-iteration FP class as string_concat;
        # Phase-7d headroom corpus).
        if (
            method == "insert"
            and self.callee_is_attribute(node)
            and self._first_arg_is_zero(node)
            and not self._receiver_reset_in_loop(node, root)
        ):
            return "list_insert_zero_in_loop"
        # ``pd.concat([acc, chunk])`` / ``pandas.concat(...)`` in a loop copies
        # the whole frame each pass -> O(n^2); collect a list and concat once.
        if root in ("pd", "pandas") and method == "concat":
            return "pd_concat_in_loop"
        return None

    def loop_iterable_call_marker(self, node: Node) -> str | None:
        """``for _, row in df.iterrows():`` — row-by-row DataFrame iteration.

        ``DataFrame.iterrows()`` boxes every row into a fresh Series, an order of
        magnitude slower than a vectorized operation (and slower than
        ``itertuples`` / ``to_dict`` when row access is unavoidable). The call
        sits in the loop header, so the body ``loop_call_marker`` misses it.
        Gated to the distinctive method name on a member-access receiver
        (``x.iterrows()``, never a bare ``iterrows(...)``) — high-precision by
        construction, like the ``pd``/``pandas`` ``concat`` root: ``iterrows`` is
        a pandas-only verb with no common collision.
        """
        if node.type != "for_statement":
            return None
        right = node.child_by_field_name("right")
        if right is None or right.type != "call":
            return None
        if self.callee_method_name(right) == "iterrows" and self.callee_is_attribute(right):
            return "pandas_iterrows_in_loop"
        return None

    def _receiver_reset_in_loop(self, node: Node, name: str) -> bool:
        """True if the call's receiver ``name`` is re-assigned (reset to a fresh
        list) inside an enclosing loop body — so the per-front-insert is bounded
        per iteration, not an O(n^2) accumulation."""
        if not name:
            return False
        cur = node.parent
        while cur is not None:
            if cur.type in ("for_statement", "while_statement"):
                body = cur.child_by_field_name("body")
                if body is not None and self._resets_name(body, name, node):
                    return True
            cur = cur.parent
        return False

    @staticmethod
    def _first_arg_is_zero(node: Node) -> bool:
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        first = next((c for c in args.children if c.is_named), None)
        return (
            first is not None
            and first.type == "integer"
            and (first.text or b"").decode("utf-8", "replace") == "0"
        )

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``x in big_list`` / ``x not in big_list`` where ``big_list`` is a
        # known list -> O(n) per probe. A set/dict membership test is O(1) and
        # must not fire, hence the ``list_names`` gate.
        if not list_names or node.type != "comparison_operator":
            return None
        # ``x in y`` is an ``in`` operator token; ``x not in y`` is a single
        # ``not in`` token. Either way it is an O(n) membership probe on a list.
        if not any(c.type in ("in", "not in") for c in node.children):
            return None
        named = [c for c in node.children if c.is_named]
        if len(named) < 2:
            return None
        right = named[-1]
        if right.type != "identifier" or right.text is None:
            return None
        name = right.text.decode("utf-8", "replace")
        return "membership_test_against_list_in_loop" if name in list_names else None

    def list_bound_names(self, root: Node) -> frozenset[str]:
        """Names assigned a provable list anywhere in the file, minus any name
        also bound to a non-list container.

        Covers ``name = [...]`` / ``name = [x for x in ...]`` / ``name =
        list(...)`` / ``name = sorted(...)``. Conservative on purpose: an opaque
        ``name = build()`` is not counted, and a name bound to a set/dict in any
        scope of the file is dropped (the ``seen``-as-list-here /
        ``seen``-as-set-there collision), so the membership marker only fires
        against a name we can prove is always a list.
        """
        list_names: set[str] = set()
        exclude: set[str] = set()
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                left = n.child_by_field_name("left")
                right = n.child_by_field_name("right")
                if (
                    left is not None
                    and left.type == "identifier"
                    and left.text is not None
                    and right is not None
                ):
                    name = left.text.decode("utf-8", "replace")
                    if self._rhs_is_list(right):
                        list_names.add(name)
                    elif self._rhs_is_nonlist_container(right):
                        exclude.add(name)
            for c in n.children:
                stack.append(c)
        return frozenset(list_names - exclude)

    @staticmethod
    def _rhs_is_list(right: Node) -> bool:
        if right.type in _PY_LIST_RHS_KINDS:
            return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and fn.text is not None:
                return fn.text.decode("utf-8", "replace") in _PY_LIST_BUILTINS
        return False

    @staticmethod
    def _rhs_is_nonlist_container(right: Node) -> bool:
        if right.type in _PY_NONLIST_RHS_KINDS:
            return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and fn.text is not None:
                return fn.text.decode("utf-8", "replace") in _PY_NONLIST_BUILTINS
        return False


DIALECT = PythonPerfDialect()
