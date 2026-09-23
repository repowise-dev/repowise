"""Python ``PerfDialect``.

Extracted verbatim from the original ``_py_sink_kind`` (``io_boundaries.py``)
and the Python branches of the walker (``_blocking_sync_api`` / ``_is_constant_for``
/ the ``_PY_STRING_KINDS`` string-concat predicate). The 6a refactor changes
zero Python behavior — the defect golden + perf suite lock that.
"""

from __future__ import annotations

import builtins
import re
from typing import TYPE_CHECKING, ClassVar

from ..loop_facts import BatchForm
from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import LoopMagnitude, SinkProbe

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
# Any of these anywhere in a read's call chain proves it is bounded, gating
# ``unbounded_read_reduced_in_memory`` (analysis.health.perf.unbounded_reduction).
PY_UNBOUNDED_READ_BOUND_METHODS: frozenset[str] = frozenset(
    {"limit", "range", "single", "maybe_single", "first", "count", "head", "aggregate"}
)

# Filesystem round-trips, in two strata for the same reason the DB verbs are
# stratified: how much evidence the name alone carries.
#
# ``PY_FS_METHODS`` are matched on the method name alone, because in this
# ecosystem only a ``pathlib.Path`` spells them — the same reasoning the TS
# dialect applies to ``readFileSync``. That matters because the dominant real
# shape binds the path to a local first (``src_path.read_text()``), so the
# receiver is an ordinary variable and ``io_names`` cannot classify it; keying
# on the module would miss nearly every genuine site. The Kotlin dialect
# deliberately excludes its ``readText`` / ``readBytes`` homonyms because
# kotlinx-io and Ktor mirror those names on in-memory buffers; Python has no
# such homonym (``io.StringIO`` and every stream type spell it ``.read()``),
# which is what makes the bare-name match safe here and not there.
#
# Metadata syscalls (``exists`` / ``is_file`` / ``is_dir`` / ``stat`` /
# ``glob``) are deliberately absent. A stat in a loop is cheap and usually
# correct, so flagging it is the obvious false-positive class — the same call
# the C++ dialect makes when it excludes buffered stdio.
PY_FS_METHODS: frozenset[str] = frozenset(
    {"read_text", "read_bytes", "write_text", "write_bytes"}
)
# ``os`` and ``shutil`` verbs, gated on the literal module root the way
# ``subprocess`` is. ``os`` is far too broad to classify wholesale (``os.environ``
# / ``os.getpid`` / ``os.path.join`` are not I/O), so it never goes through
# ``io_names``; the root name plus an explicit verb set is the whole gate.
#
# CEILING: ``callee_root_name`` resolves the LEFTMOST identifier of the whole
# callee chain, so ``os.path.join(a, b).verb()`` also arrives here as
# ``root == "os"``. The verb set must therefore hold no name that a ``str`` /
# ``list`` / ``dict`` also answers to. ``replace`` was the one that did, and it
# was a live false positive on the canonical Windows path idiom
# (``os.path.join(root, name).replace("\\", "/")`` — five instances across the
# OSS corpus, each yielding a bogus ``io_in_loop`` AND a ``nested_loop_with_io``).
# It is omitted at near-zero recall cost: ``os.replace`` is atomic ``os.rename``,
# which is already listed. Upgrade path if this set ever needs a colliding verb:
# thread the callee's dotted segment count into ``sink_kind`` and require
# exactly two, which is the real invariant being approximated here.
PY_OS_FS_METHODS: frozenset[str] = frozenset(
    {
        "walk",
        "listdir",
        "scandir",
        "remove",
        "unlink",
        "rename",
        "makedirs",
        "mkdir",
        "rmdir",
        "removedirs",
    }
)
PY_SHUTIL_FS_METHODS: frozenset[str] = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "rmtree",
        "make_archive",
        "unpack_archive",
    }
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

# Promotion facts (perf/loop_facts.py). Calls and attributes that read a query
# result out without changing how many rows it holds.
_PY_RESULT_PROJECTIONS: frozenset[str] = frozenset(
    {"all", "scalars", "fetchall", "json", "data", "rows"}
)
_PY_BOUND_NAME_RE = re.compile(r"(?i)retr|attempt")
# Raw SQL capped in its own text: ``sa.text("... LIMIT 1000")``.
_PY_SQL_LIMIT_RE = re.compile(rb"(?is)\bselect\b.*\blimit\s+\d+\b")
_PY_SQL_COMMENT_RE = re.compile(rb"--[^\n]*|/\*.*?\*/", re.S)
# A per-key call that limits or orders its rows is not the read one IN query makes.
_PY_PER_KEY_LIMITS: frozenset[str] = frozenset(
    {
        "limit", "single", "maybe_single", "first", "one", "scalar_one",
        "scalar_one_or_none", "order", "order_by", "range", "offset",
    }
)
_PY_ONE_ROW: frozenset[str] = _PY_PER_KEY_LIMITS | {"scalar"}
_PY_READ_CAPS: frozenset[str] = _PY_ONE_ROW - {"order", "order_by", "offset"}
_PY_WRITES: frozenset[str] = frozenset({"update", "insert", "upsert", "values"})
_PY_SCOPES: frozenset[str] = frozenset({"function_definition", "lambda", "class_definition"})
_PY_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_PY_SEM_NAME_RE = re.compile(r"(?i)(sem|semaphore|limiter|limit)$")
# A model class is CapWords; an ALL_CAPS constant (``API_URL``) is not.
_PY_MODEL_NAME_RE = re.compile(r"^[A-Z]\w*[a-z]\w*$")


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
            # Centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # Python-specific quadratic anti-patterns.
            "list_insert_zero_in_loop",
            "pd_concat_in_loop",
            # Header-call marker: the slow iterable IS the loop driver.
            "pandas_iterrows_in_loop",
        }
    )

    string_literal_kinds = _PY_STRING_KINDS
    aug_assign_kinds = _PY_AUG_ASSIGN_KINDS
    # Everything whose cost is bounded by ONE inode: a single file read/write, a
    # single unlink/mkdir/rename, one directory listing. Outside a loop that is
    # ordinary work, not a latency risk, so it must not reach ``hot_path_sync_io``
    # (see ``BasePerfDialect.hot_path_excluded_methods``). What survives is the
    # unbounded set — a whole-tree walk, a recursive copy/delete, an archive
    # round-trip — plus the bare ``open(...)`` builtin the marker was originally
    # calibrated on. Keyed on the method name alone, so a same-named non-``os``
    # receiver is excluded too: recall-only, never a false positive.
    hot_path_excluded_methods = (
        PY_FS_METHODS
        | (PY_OS_FS_METHODS - {"walk"})
        | (PY_SHUTIL_FS_METHODS - {"copytree", "rmtree", "make_archive", "unpack_archive"})
    )

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
        # on every backoff/poll loop, measured across a Python corpus. No legitimate
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
        if is_attribute and method in PY_FS_METHODS:
            # ``p.read_text()`` / ``Path(x).write_bytes(...)``. ``is_attribute``
            # mirrors the DB branches: pathlib always spells these as a member
            # call, so requiring it costs no recall and drops a bare local
            # helper that happens to share the name.
            return "filesystem"
        if root == "os" and method in PY_OS_FS_METHODS:
            return "filesystem"
        if root == "shutil" and method in PY_SHUTIL_FS_METHODS:
            return "filesystem"
        if method == "urlopen":
            return "network"
        if is_attribute and root_kind == "network" and method in HTTP_VERBS:
            # ``requests.get(...)`` is a member call on an imported client; a bare
            # ``get(...)`` (e.g. a builtin/local) never has the verb-call shape, so
            # gate on ``is_attribute`` like the DB branches above.
            return "network"
        if awaited and root_kind == "network":
            return "network"
        return None

    def call_sink_kind(
        self, call: Node, *, awaited: bool, io_names: dict[str, str], has_db_import: bool
    ) -> str | None:
        kind = super().call_sink_kind(
            call, awaited=awaited, io_names=io_names, has_db_import=has_db_import
        )
        if kind == "db" and self.callee_method_name(call) in PY_DB_AMBIGUOUS:
            # ``plugins.all()`` on an imported or module-global name is a registry,
            # not a query result: a result is bound in the function that reads it.
            fn = call.child_by_field_name("function")
            receiver = fn.child_by_field_name("object") if fn is not None else None
            if receiver is not None and receiver.type == "identifier":
                name = receiver.text or b""
                if io_names.get(name.decode()) != "db" and not self._bound_locally(call, name):
                    return None
        orm_get = self._orm_get(call) if kind is None else None
        if orm_get is not None:
            # ``get`` alone is ``dict.get`` / ``requests.get``; the model-class shape
            # plus db evidence in the file is what makes it ``Session.get``.
            db = has_db_import or io_names.get(self.callee_root_name(call) or "") == "db"
            return "db" if db and not self._key_used_earlier(call, orm_get[1]) else None
        return kind

    def _bound_locally(self, call: Node, name: bytes) -> bool:
        """*name* is assigned before *call* in its function, or is a parameter of it."""
        if self._reaching_rhs(call, name) is not None:
            return True
        scope = call.parent
        while scope is not None and scope.type != "function_definition":
            scope = scope.parent
        params = scope.child_by_field_name("parameters") if scope is not None else None
        return params is not None and name.decode() in self._param_names(params)

    def _key_used_earlier(self, call: Node, key: Node) -> bool:
        """An earlier call in this loop body gave the same session the same key, so the
        row may already sit in the session and ``get`` answers from memory."""
        loop = call.parent
        while loop is not None and loop.type not in ("for_statement", "while_statement"):
            if loop.type in _PY_SCOPES:
                return False
            loop = loop.parent
        body = loop.child_by_field_name("body") if loop is not None else None
        session = (self.callee_root_name(call) or "").encode()
        for node in self._walk(body, prune=_PY_SCOPES) if body is not None else ():
            args = node.child_by_field_name("arguments") if node.type == "call" else None
            if args is None or node.end_byte > call.start_byte:
                continue
            texts = {arg.text for arg in args.children}
            same_session = session in texts or self.callee_root_name(node) == session.decode()
            if key.text in texts and same_session:
                return True
        return False

    def _orm_get(self, call: Node) -> tuple[str, Node] | None:
        """``(Model, key)`` of a ``x.get(Model, key)`` primary-key lookup."""
        if self.callee_method_name(call) != "get" or not self.callee_is_attribute(call):
            return None
        args = call.child_by_field_name("arguments")
        named = [c for c in args.children if c.is_named] if args is not None else []
        if any(c.type in ("list_splat", "dictionary_splat") for c in named):
            return None
        positional = [c for c in named if c.type != "keyword_argument"]
        model = self._dotted_path(positional[0]) if len(positional) == 2 else None
        if model is None or not _PY_MODEL_NAME_RE.match(model.rsplit(".", 1)[-1]):
            return None
        return model, positional[1]

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

    # ``itertools.batched``, ``more_itertools.chunked`` and hand-rolled peers.
    _CHUNKING_CALLS: frozenset[str] = frozenset(
        {"batched", "chunked", "ichunked", "chunks", "iter_chunks", "grouper"}
    )

    def is_chunked_loop(self, node: Node) -> bool:
        """``range(start, stop, step)`` with a non-unit step, or a chunking helper."""
        if node.type != "for_statement":
            return False
        right = node.child_by_field_name("right")
        if right is None or right.type != "call":
            return False
        fn = right.child_by_field_name("function")
        if fn is None or fn.text is None:
            return False
        name = fn.text.decode("utf-8", "replace").rsplit(".", 1)[-1]
        if name in self._CHUNKING_CALLS:
            return True
        if name != "range":
            return False
        args = right.child_by_field_name("arguments")
        named = [c for c in args.children if c.is_named] if args is not None else []
        return len(named) == 3 and named[2].text not in (b"1", b"-1")

    def is_string_concat(self, node: Node) -> bool:
        """``s += "x"`` accumulation — but skip an accumulator that is *reset*
        each iteration of an enclosing loop.

        ``buf = base[:N]; ... buf += part`` inside a loop builds a fresh, bounded
        string per iteration (not the O(n^2) cross-iteration accumulation the
        marker targets), so it is a false positive. Measured on a Python corpus:
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

    def unbounded_read_bound_methods(self) -> frozenset[str]:
        return PY_UNBOUNDED_READ_BOUND_METHODS

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
        # measured on a Python corpus).
        # ``collections.deque.insert(0, x)`` is O(1) front-insertion by design (it
        # is the deque's own ``appendleft`` machinery), so exclude a receiver
        # provably constructed via ``deque()`` / ``collections.deque(...)``.
        if (
            method == "insert"
            and self.callee_is_attribute(node)
            and self._first_arg_is_zero(node)
            and not self._receiver_reset_in_loop(node, root)
            and not self._receiver_bound_to_deque(node, root)
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
        (``x.iterrows()``, never a bare ``iterrows(...)``) AND to a file that
        imports pandas (same style as the ``pd``/``pandas`` root gate in
        ``pd_concat``). Residual limitation: this is still a name match — a
        user-defined class with an ``iterrows`` method in a file that also
        imports pandas would still be flagged.
        """
        if node.type != "for_statement":
            return None
        right = node.child_by_field_name("right")
        if right is None or right.type != "call":
            return None
        if (
            self.callee_method_name(right) == "iterrows"
            and self.callee_is_attribute(right)
            and self._file_imports_pandas(node)
        ):
            return "pandas_iterrows_in_loop"
        return None

    @staticmethod
    def _file_imports_pandas(node: Node) -> bool:
        """True if the enclosing file has an ``import pandas`` / ``from pandas
        import ...`` statement (a soft gate; still a name match, see caller)."""
        root = node
        while root.parent is not None:
            root = root.parent
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if "import" in n.type:
                txt = (n.text or b"").decode("utf-8", "replace")
                if "pandas" in txt:
                    return True
                continue  # imports do not nest further imports
            stack.extend(n.children)
        return False

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

    def _receiver_bound_to_deque(self, node: Node, name: str) -> bool:
        """True if the call's receiver ``name`` is bound to a ``deque()`` /
        ``collections.deque(...)`` anywhere in the file. Mirrors the whole-tree
        binding scan of :meth:`list_bound_names`: a scoped rewrite is out of
        scope, so a same-named list in another scope would still be missed here —
        acceptable, since dropping the finding only trades recall for precision.
        """
        if not name:
            return False
        root = node
        while root.parent is not None:
            root = root.parent
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
                    and left.text.decode("utf-8", "replace") == name
                    and right is not None
                    and self._rhs_is_deque(right)
                ):
                    return True
            stack.extend(n.children)
        return False

    @staticmethod
    def _rhs_is_deque(right: Node) -> bool:
        if right.type != "call":
            return False
        fn = right.child_by_field_name("function")
        if fn is None or fn.text is None:
            return False
        callee = fn.text.decode("utf-8", "replace")
        return callee == "deque" or callee.endswith(".deque")

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
            elif n.type == "parameters":
                # A name also used as a function parameter has no proven binding
                # inside that scope (the caller could pass a set), so it collides
                # with an unrelated module-level list of the same name. Exclude it,
                # mirroring the set/dict-literal exclusion above.
                exclude.update(self._param_names(n))
            for c in n.children:
                stack.append(c)
        return frozenset(list_names - exclude)

    @staticmethod
    def _param_names(params: Node) -> set[str]:
        """The bound names declared in a ``parameters`` node (plain / typed /
        default / splat forms), excluding any type-annotation identifiers."""
        names: set[str] = set()
        for p in params.children:
            if not p.is_named:
                continue
            if p.type == "identifier":
                if p.text is not None:
                    names.add(p.text.decode("utf-8", "replace"))
                continue
            nm = p.child_by_field_name("name")
            if nm is None:
                # typed_parameter has no ``name`` field: the param name is its
                # first identifier child (the type comes after the ``:``).
                nm = next((c for c in p.children if c.type == "identifier"), None)
            if nm is not None and nm.text is not None:
                names.add(nm.text.decode("utf-8", "replace"))
        return names

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

    # -- promotion facts (perf/loop_facts.py) ---------------------------------

    await_kind = "await"
    key_hops: ClassVar[dict[str, str]] = {"attribute": "object", "subscript": "value"}
    binding_fields: ClassVar[dict[str, str]] = {
        "assignment": "left",
        "augmented_assignment": "left",
        "for_statement": "left",
        "named_expression": "name",
    }
    branch_kinds = frozenset(
        {
            "if_statement", "try_statement", "match_statement", "while_statement",
            "conditional_expression", "boolean_operator",
        }
    )
    exit_kinds = frozenset(
        {"break_statement", "continue_statement", "return_statement", "raise_statement"}
    )
    scope_kinds = _PY_SCOPES
    sequence_appends = frozenset({"append", "extend", "insert"})

    def loop_magnitude(self, loop: Node, probe: SinkProbe) -> LoopMagnitude | None:
        if loop.type != "for_statement":
            return None
        return self._magnitude(loop.child_by_field_name("right"), loop, probe, hop=True)

    def _magnitude(
        self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        """Grows (a query result or a directory listing), bounded, or ``None``.

        *hop* allows one step through the name's last assignment before the
        loop; a parameter has none and stays unknown on purpose.
        """
        if expr is None:
            return None
        if expr.type in ("await", "parenthesized_expression"):
            inner = next((c for c in expr.children if c.is_named), None)
            return self._magnitude(inner, loop, probe, hop=hop)
        if expr.type == "identifier" and hop and expr.text:
            rhs = self._reaching_rhs(loop, expr.text)
            return self._magnitude(rhs, loop, probe, hop=False)
        if expr.type == "subscript":
            return self._slice_magnitude(expr)
        if expr.type == "boolean_operator" and any(c.type == "or" for c in expr.children):
            # ``(await q.execute()).data or []``: the fallback is empty, the read is not.
            left = expr.child_by_field_name("left")
            return self._grows(self._magnitude(left, loop, probe, hop=hop))
        if expr.type == "attribute":
            attr = expr.child_by_field_name("attribute")
            if attr is None or (attr.text or b"").decode() not in _PY_RESULT_PROJECTIONS:
                return None
            return self._grows(self._magnitude(expr.child_by_field_name("object"), loop, probe, hop=hop))
        return self._call_magnitude(expr, loop, probe, hop=hop) if expr.type == "call" else None

    def _call_magnitude(
        self, expr: Node, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        method = self.callee_method_name(expr) or ""
        member = self.callee_is_attribute(expr)
        if method == "range" and not member:
            return self._range_magnitude(expr, loop, probe, hop=hop)
        if probe(expr) in ("db", "network"):
            # A read capped in the query is as large as its cap; a statement or result
            # built before the loop (``stmt = ....limit(n)``) is read one assignment back.
            names = [n.text for n in self._walk(expr) if n.type == "identifier"]
            parts = [expr, *filter(None, (self._reaching_rhs(loop, name) for name in names))]
            capped = any(self._caps_read(n, loop) for part in parts for n in self._walk(part))
            return None if capped else "grows_with_data"
        if member and method in _PY_RESULT_PROJECTIONS:
            receiver = expr.child_by_field_name("function").child_by_field_name("object")
            return self._grows(self._magnitude(receiver, loop, probe, hop=hop))
        return None

    def _caps_read(self, node: Node, loop: Node) -> bool:
        """``.limit(n)`` and peers, a SQL string with ``LIMIT n``, or ``.in_()`` over one chunk of keys
        (``xs[i:i + N]``, or the element of a loop that walks ``xs`` in chunks)."""
        if node.type == "string":
            return bool(_PY_SQL_LIMIT_RE.search(_PY_SQL_COMMENT_RE.sub(b"", node.text or b"")))
        method = self.callee_method_name(node) if node.type == "call" else None
        if method in _PY_READ_CAPS:
            return True
        args = node.child_by_field_name("arguments") if method == "in_" else None
        keys = next((c for c in args.children if c.is_named), None) if args else None
        if keys is None:
            return False
        if keys.type == "subscript":
            return self._slice_magnitude(keys) == "bounded"
        outer = loop
        while outer is not None and keys.type == "identifier":
            target = outer.child_by_field_name("left") if outer.type == "for_statement" else None
            if target is not None and target.text == keys.text and self.is_chunked_loop(outer):
                return True
            outer = outer.parent
        return False

    def _range_magnitude(
        self, call: Node, loop: Node, probe: SinkProbe, *, hop: bool
    ) -> LoopMagnitude | None:
        """``range(len(rows))`` grows with ``rows``; ``range(MAX_RETRIES)`` is bounded."""
        args = [c for c in call.child_by_field_name("arguments").children if c.is_named]
        if not args:
            return None
        stop = args[1] if len(args) > 1 else args[0]
        if stop.type == "call" and self.callee_method_name(stop) == "len":
            inner = [c for c in stop.child_by_field_name("arguments").children if c.is_named]
            if len(inner) == 1:
                return self._grows(self._magnitude(inner[0], loop, probe, hop=hop))
            return None
        path = self._dotted_path(stop) if stop.type in ("identifier", "attribute") else None
        last = path.rsplit(".", 1)[-1] if path else ""
        if (stop.type == "identifier" and last.isupper() and len(last) > 1) or (
            last and _PY_BOUND_NAME_RE.search(last)
        ):
            return "bounded"
        return None

    def _slice_magnitude(self, subscript: Node) -> LoopMagnitude | None:
        """``xs[:5]`` / ``xs[:LIMIT]`` / ``xs[i:i + 4]``: a constant-width slice."""
        piece = subscript.child_by_field_name("subscript")
        if piece is None or piece.type != "slice" or sum(c.type == ":" for c in piece.children) != 1:
            return None
        colon = next(i for i, c in enumerate(piece.children) if c.type == ":")
        start = next((c for c in piece.children[:colon] if c.is_named), None)
        stop = next((c for c in piece.children[colon + 1 :] if c.is_named), None)
        if stop is None:
            return None
        if start is None:
            text = (stop.text or b"").decode()
            constant = stop.type == "integer" or (
                stop.type == "identifier" and text.isupper() and len(text) > 1
            )
            return "bounded" if constant else None
        if stop.type == "binary_operator":
            left, right = stop.child_by_field_name("left"), stop.child_by_field_name("right")
            same_start = left is not None and self._dotted_path(left) == self._dotted_path(start)
            if same_start and right is not None and right.type == "integer":
                return "bounded"
        return None

    def _reaching_rhs(self, loop: Node, name: bytes) -> Node | None:
        """The right side of the last ``name = ...`` before *loop* in its function."""
        scope = loop.parent
        while scope is not None and scope.type != "function_definition":
            scope = scope.parent
        if scope is None:
            return None
        last: Node | None = None
        for node in self._walk(scope, prune=_PY_SCOPES):
            if node.type != "assignment" or node.end_byte > loop.start_byte:
                continue
            left = node.child_by_field_name("left")
            named = left is not None and left.type == "identifier" and left.text == name
            if named and (last is None or node.start_byte > last.start_byte):
                last = node
        return last.child_by_field_name("right") if last is not None else None

    def batch_form(self, sink: Node, loop: Node, probe: SinkProbe) -> BatchForm | None:
        target = loop.child_by_field_name("left")
        if loop.type != "for_statement" or target is None or target.type != "identifier":
            return None
        body = self.loop_body(loop) or loop
        iterable = self.loop_iterable_name(loop)
        for node in self._walk(body):
            if (
                node.type == "call"
                and self.callee_is_attribute(node)
                and self.callee_root_name(node) == iterable
                and self.callee_method_name(node) in self.sequence_appends
            ):
                return None  # a worklist: its keys are not known before the loop
        refs = [n for n in self._walk(sink) if n.type == "identifier" and n.text == target.text]
        methods = {self.callee_method_name(n) for n in self._walk(sink) if n.type == "call"}
        if len(refs) != 1 or methods & _PY_WRITES or self._reads_loop_local(sink, body):
            return None
        call = self._keyed_filter(sink, refs[0], methods)
        if call is None:
            return None
        if "delete" in methods:
            # Batched, a delete also removes rows a skipped iteration would have left.
            kept = self._unconditional(sink, body)
        else:
            kept = not self._builds_in_order(body)
        if self._orm_get(sink) is not None:
            # ``get`` answers from the session's identity map when the row is already
            # loaded, which no syntax shows: held out, half of these sites made no
            # round trip. The bulk form is named, never proven.
            kept = False
        equivalent = (
            kept
            and not methods & _PY_PER_KEY_LIMITS
            and not self._limited_downstream(sink, body)
            and self._only_io_in_body(sink, body, probe)
            and not self._calls_for_effect(sink, body)
        )
        return BatchForm(call, equivalent)

    def _calls_for_effect(self, sink: Node, body: Node) -> bool:
        """Another statement calls something this function did not build as a list, set
        or dict (``run_task(doc)``, ``session.add(r)``, ``t = task.delay(d)``), which may
        write what the batched read would read before it ran. ``seen.add(x)`` is fine, and
        so is an assigned builtin or method on a local (``key = str(r.id)``)."""
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
            if not self.callee_is_attribute(call):
                if not (assigned and self.callee_method_name(call) in _PY_BUILTINS):
                    return True
                continue
            root = self.callee_root_name(call) or ""
            rhs = self._reaching_rhs(call, root.encode())
            if rhs is None or not (
                assigned or self._rhs_is_list(rhs) or self._rhs_is_nonlist_container(rhs)
            ):
                return True
        return False

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
                # supabase-py: ``.table(t).select(...).eq("col", key)``
                args = [c for c in node.child_by_field_name("arguments").children if c.is_named]
                if (
                    len(args) == 2
                    and args[0].type == "string"
                    and self._is_key_of(args[1], ref)
                    and ("select" in methods) != ("delete" in methods)
                ):
                    column = next(
                        (c.text.decode() for c in args[0].children if c.type == "string_content"),
                        None,
                    )
                    return f'.in_("{column}", keys)' if column else None
            if node.type == "comparison_operator" and any(c.type == "==" for c in node.children):
                # SQLAlchemy: ``.where(M.col == key)`` / ``.filter(M.col == key)``
                sides = [c for c in node.children if c.is_named]
                if len(sides) != 2:
                    continue
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
                clause = next((c for c in cur.children if c.type == "with_clause"), None)
                for item in clause.children if clause is not None else ():
                    value = item.child_by_field_name("value") if item.type == "with_item" else None
                    # A name, not a constructor: ``async with Semaphore(5)`` in the
                    # loop makes a fresh bound per iteration, which bounds nothing.
                    path = self._dotted_path(value) if value is not None else None
                    if path and _PY_SEM_NAME_RE.search(path.rsplit(".", 1)[-1]):
                        return path
            cur = cur.parent
        return None


DIALECT = PythonPerfDialect()
