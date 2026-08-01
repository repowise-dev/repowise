"""C++ ``PerfDialect``.

Flagship: a ``sqlite3_step`` / ``fopen`` / socket round-trip inside a
``for (auto& x : xs)`` loop, plus ``std::regex`` construction per iteration —
building a ``regex`` compiles the pattern into a program and is famously the
expensive half of using one, so hoisting it is a real, mechanical fix.

This dialect is **deliberately narrow**. It was tuned by hand-verifying every
finding it produced over leveldb, fmt, Crow, nlohmann-json and abseil-cpp
(~547k lines), and three markers other languages carry were dropped outright
because each is a guaranteed false positive in C++ — see :attr:`markers` for
the reasoning on ``string_concat_in_loop``, ``resource_construction_in_loop``
and ``blocking_io_under_lock``. C++ has no ``async``/``await`` either, so
``blocking_sync_in_async`` is absent rather than faked onto ``std::async``.

Grammar seams, verified against the installed tree-sitter-cpp:

* a scoped call (``std::filesystem::remove()``) has a ``qualified_identifier``
  callee whose leftmost segment is the useless ``std``, so
  :meth:`callee_root_name` returns the QUALIFIER segment instead
  (``std::filesystem::remove`` -> ``filesystem``), mirroring the Rust dialect's
  answer to the same problem;
* a constructor with arguments in a declaration (``std::regex re(pat);``) is
  **not** a call node at all — it is a ``declaration`` whose ``type`` field
  names the constructed type — so it is matched by :meth:`loop_stmt_marker`;
* ``new Foo()`` is a ``new_expression``, not a ``call_expression``.

Two properties that cap recall (never precision):

* **The C free functions fire only when truly unqualified.** ``root == method``
  is the test. A namespaced call is someone else's API that merely shares a
  POSIX name — ``json::accept`` matched the socket verb, ``std::fprintf`` the
  stdio one. The generic verbs ``read`` / ``write`` are excluded even bare.
* **No I/O-import evidence.** ``collect_io_names`` keys on import nodes whose
  type contains "import" (plus a few named forms); a C++ ``#include`` is a
  ``preproc_include`` and classifies nothing, so ``io_names`` /
  ``has_db_import`` are always empty here. Every entry in this lexicon is
  therefore distinctive enough to fire un-gated — there is no ambiguous,
  evidence-gated stratum for C++ the way there is for Java or Python.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# POSIX filesystem round-trips — unqualified free functions that reach the
# kernel on every call.
#
# The *buffered* stdio family (``fprintf`` / ``fputs`` / ``fgets`` / ``fread`` /
# ``fwrite`` / ``fscanf``) is deliberately excluded: those write into a FILE*
# buffer, not to the device, so a loop of them is not N round-trips.
# Smoke-testing leveldb, ~30 of 39 ``io_in_loop`` hits were
# ``std::fprintf(stderr, …)`` diagnostics inside benchmark loops — the single
# largest false-positive class this dialect produced.
#
# Bare ``read`` / ``write`` are excluded for the same reason: they are the
# universal buffer/stream verb in C++ (fmt's own internal ``write(out, sv)``
# matched), and no qualifier is present to tell POSIX from a local helper.
# ``pread`` / ``pwrite`` / ``fsync`` carry no such ambiguity. ``fclose`` is
# excluded as duplicate signal — the paired ``fopen`` already flags the site.
C_FS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "fopen",
        "freopen",
        "open",
        "creat",
        "pread",
        "pwrite",
        "stat",
        "fstat",
        "lstat",
        "opendir",
        "readdir",
        "mkdir",
        "rmdir",
        "unlink",
        "truncate",
        "ftruncate",
        "fsync",
        "fdatasync",
    }
)
# ``std::filesystem`` free functions (root is the ``filesystem`` / ``fs``
# qualifier segment supplied by ``callee_root_name``).
STD_FILESYSTEM_ROOTS: frozenset[str] = frozenset({"filesystem", "fs"})
STD_FILESYSTEM_METHODS: frozenset[str] = frozenset(
    {
        "exists",
        "file_size",
        "remove",
        "remove_all",
        "rename",
        "copy",
        "copy_file",
        "create_directory",
        "create_directories",
        "directory_iterator",
        "recursive_directory_iterator",
        "last_write_time",
        "resize_file",
        "space",
        "status",
    }
)
# BSD sockets — only the spellings that cannot be an implicit-``this`` member
# call.
#
# In C++ a member function called from inside its own class is spelled
# unqualified, so ``send(pkt)`` / ``connect(addr)`` / ``bind(x)`` / ``listen()``
# / ``accept()`` / ``recv()`` are indistinguishable from the POSIX free
# functions without type resolution — seastar's ``ipv4::get_packet`` calls its
# own ``send(...)`` exactly this way. Those six are therefore excluded; the
# suffixed forms below are not plausible member names.
C_NET_FUNCTIONS: frozenset[str] = frozenset(
    {
        "socket",
        "sendto",
        "sendmsg",
        "recvfrom",
        "recvmsg",
        "getaddrinfo",
        "gethostbyname",
        "setsockopt",
        "getsockopt",
    }
)
# libcurl — the dominant C/C++ HTTP client.
CURL_FUNCTIONS: frozenset[str] = frozenset({"curl_easy_perform", "curl_easy_setopt"})
# Subprocess spawning.
C_SUBPROCESS_FUNCTIONS: frozenset[str] = frozenset(
    {"system", "popen", "fork", "execl", "execlp", "execle", "execv", "execvp", "execvpe"}
)
# Embedded / client database round-trips. Every name carries its library
# prefix, so none of them can collide with ordinary application code.
DB_FUNCTIONS: frozenset[str] = frozenset(
    {
        "sqlite3_open",
        "sqlite3_open_v2",
        "sqlite3_exec",
        "sqlite3_step",
        "sqlite3_prepare",
        "sqlite3_prepare_v2",
        "sqlite3_get_table",
        "mysql_query",
        "mysql_real_query",
        "mysql_store_result",
        "mysql_use_result",
        "mysql_real_connect",
        "PQexec",
        "PQexecParams",
        "PQprepare",
        "PQconnectdb",
        "PQgetResult",
        "leveldb_get",
        "leveldb_put",
        "rocksdb_get",
        "rocksdb_put",
    }
)

# A ``std::regex`` built per iteration recompiles the pattern program.
REGEX_TYPES: frozenset[str] = frozenset({"regex", "wregex", "basic_regex"})
# ``std::mutex`` scope guards — taking the lock every iteration is contention.
LOCK_GUARD_TYPES: frozenset[str] = frozenset(
    {"lock_guard", "unique_lock", "scoped_lock", "shared_lock"}
)
LOCK_METHODS: frozenset[str] = frozenset({"lock", "lock_shared"})

_STRING_KINDS: frozenset[str] = frozenset(
    {"string_literal", "raw_string_literal", "concatenated_string"}
)


def _qualified_segments(call_node: Node) -> list[str] | None:
    """Segments of a ``qualified_identifier`` callee (``std::filesystem::remove``
    -> ``['std', 'filesystem', 'remove']``), or ``None`` for a bare / member
    call. Template arguments are stripped (``std::make_unique<T>`` -> the
    ``make_unique`` segment)."""
    fn = call_node.child_by_field_name("function")
    if fn is None or fn.type != "qualified_identifier" or fn.text is None:
        return None
    txt = fn.text.decode("utf-8", "replace").split("<")[0]
    segs = [s for s in txt.split("::") if s]
    return segs or None


def _declared_type_name(node: Node) -> str | None:
    """Last segment of a ``declaration``'s type (``std::ifstream f(p);`` ->
    ``ifstream``, ``std::lock_guard<std::mutex> g(m);`` -> ``lock_guard``)."""
    ty = node.child_by_field_name("type")
    if ty is None or ty.text is None:
        return None
    return ty.text.decode("utf-8", "replace").split("<")[0].split("::")[-1].strip()


def _declaration_has_initializer(node: Node) -> bool:
    """True if the declaration actually constructs something (``T x(a);`` /
    ``T x{a};`` / ``T x = f();``) rather than default-declaring (``T x;``).

    A bare ``std::ifstream f;`` opens no file, so it must not fire; the two
    constructing shapes are an ``init_declarator`` (with a ``value``) and the
    most-vexing-parse ``function_declarator`` the grammar produces for
    ``T x(ident);``.
    """
    for child in node.named_children:
        if child.type in ("init_declarator", "function_declarator"):
            return True
    return False


class CppPerfDialect(BasePerfDialect):
    language = "cpp"
    markers = frozenset(
        {
            "io_in_loop",
            "regex_compile_in_loop",
            "lock_in_loop",
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # Three markers are deliberately ABSENT — each would be a
            # guaranteed-false-positive for this language:
            #
            # ``string_concat_in_loop`` — ``std::string::operator+=`` appends in
            #   place into a geometrically-grown buffer (amortized O(1)); it does
            #   NOT rebuild an immutable string the way Java/Python/JS ``+=``
            #   does. Exactly the reasoning that keeps this marker out of the
            #   Rust dialect. All 29 hits it produced across leveldb / Crow /
            #   fmt / nlohmann-json were correct, idiomatic append loops.
            #
            # ``resource_construction_in_loop`` — the two candidate shapes both
            #   fail: a loop-constructed ``std::ifstream`` is opened over a
            #   per-iteration PATH (nothing to hoist), and ``std::thread`` in a
            #   loop is how you build a thread POOL (abseil's own
            #   ``thread_pool.h`` was flagged by it). No C++ construction shape
            #   left where "hoist it out of the loop" is sound advice.
            #
            # ``blocking_io_under_lock`` — C++'s lock is RAII-scoped (a
            #   ``lock_guard`` declaration holds to the end of the ENCLOSING
            #   block), so no node's body is exactly the held region; see
            #   :meth:`is_lock_scope`.
        }
    )

    # -- callee extraction ----------------------------------------------------

    def callee_root_name(self, call_node: Node) -> str | None:
        """For a scoped call, the QUALIFIER segment (the one before the called
        name): ``std::filesystem::remove`` -> ``filesystem``. For member calls
        (``db.execute()``) and bare calls (``fopen()``) the base extraction — the
        receiver / function identifier — is already right."""
        if call_node.type == "new_expression":
            ty = call_node.child_by_field_name("type")
            if ty is not None and ty.text:
                return ty.text.decode("utf-8", "replace").split("<")[0].split("::")[-1]
            return None
        segs = _qualified_segments(call_node)
        if segs is not None and len(segs) >= 2:
            return segs[-2]
        return super().callee_root_name(call_node)

    def callee_method_name(self, call_node: Node) -> str | None:
        if call_node.type == "new_expression":
            return self.callee_root_name(call_node)
        segs = _qualified_segments(call_node)
        if segs is not None:
            return segs[-1]
        return super().callee_method_name(call_node)

    def callee_is_attribute(self, call_node: Node) -> bool:
        if call_node.type == "new_expression":
            return True
        return super().callee_is_attribute(call_node)

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
        # Library-prefixed database entry points — distinctive by construction.
        if method in DB_FUNCTIONS:
            return "db"
        if root in STD_FILESYSTEM_ROOTS and method in STD_FILESYSTEM_METHODS:
            return "filesystem"
        if method in CURL_FUNCTIONS:
            return "network"
        # The C free functions below are only unambiguous when the callee is a
        # *truly unqualified* identifier. A member call (``buf.write()`` /
        # ``set.remove()``) is ordinary container vocabulary, and a NAMESPACED
        # call is someone else's API that merely shares the name — smoke-testing
        # nlohmann-json produced ``json::accept(…)`` matching the socket verb
        # ``accept``, and leveldb produced ``std::remove`` / ``std::fprintf``.
        # Requiring no qualifier at all closes both.
        # (``root == method`` is exactly the unqualified case: the base
        # extraction returns a bare call's own name as its root, while a scoped
        # call's root is the qualifier segment — ``std::fprintf`` -> ``std``.)
        if is_attribute or root != method:
            return None
        if method in C_SUBPROCESS_FUNCTIONS:
            return "subprocess"
        if method in C_NET_FUNCTIONS:
            return "network"
        if method in C_FS_FUNCTIONS:
            return "filesystem"
        return None

    # -- loops ----------------------------------------------------------------

    def is_constant_loop(self, node: Node) -> bool:
        """``for (int i = 0; i < 8; i++)`` with a literal bound is a compile-time
        count, not a data-dependent multiplier. A range-for always iterates data,
        and ``while`` / ``do`` bounds are opaque."""
        if node.type != "for_statement":
            return False
        cond = node.child_by_field_name("condition")
        if cond is None or cond.type != "binary_expression":
            return False
        right = cond.child_by_field_name("right")
        return right is not None and right.type == "number_literal"

    def is_iteration_loop(self, node: Node) -> bool:
        """Only a range-for provably multiplies over a collection. A C-style
        ``for`` may be either a collection walk or a cursor and a ``while`` is a
        cursor, so neither opens the nested-loop markers (precision-first: this
        gate only ever suppresses)."""
        return node.type == "for_range_loop"

    def loop_iterable_name(self, node: Node) -> str | None:
        """The collection a ``for (auto& x : coll)`` walks — the same-collection
        ``nested_loop_quadratic`` shape gate."""
        if node.type != "for_range_loop":
            return None
        return self._dotted_path(node.child_by_field_name("right"))

    # -- extra loop markers ---------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``std::regex(pat)`` written as an expression / ``new std::regex(pat)``.
        # Only a literal pattern is unambiguously hoistable — the same gate Go,
        # Java and Rust use.
        if method in REGEX_TYPES and self._has_literal_pattern(node):
            return "regex_compile_in_loop"
        # ``mu.lock()`` on a receiver (a bare ``lock()`` is not a mutex).
        if method in LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        return None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        """Constructor-in-declaration markers: ``std::regex re("^x$");`` is a
        ``declaration``, not a call, so it never reaches the call hooks."""
        if node.type != "declaration" or not _declaration_has_initializer(node):
            return None
        name = _declared_type_name(node)
        if name is None:
            return None
        if name in REGEX_TYPES:
            return "regex_compile_in_loop" if self._has_literal_pattern(node) else None
        if name in LOCK_GUARD_TYPES:
            return "lock_in_loop"
        return None

    @staticmethod
    def _has_literal_pattern(node: Node) -> bool:
        """True when the construction's first argument is a compile-time string
        literal — ``std::regex re("^x$")`` is hoistable, ``std::regex re(pat)``
        may legitimately vary per iteration.

        Covers both spellings the grammar produces: an ``argument_list`` (a call
        or ``new``) and the ``init_declarator``/``function_declarator`` argument
        list of a declaration.
        """
        stack = [node]
        for _ in range(8):
            if not stack:
                return False
            cur = stack.pop()
            if cur.type in ("argument_list", "parameter_list", "initializer_list"):
                first = next((c for c in cur.children if c.is_named), None)
                return first is not None and first.type in _STRING_KINDS
            stack.extend(c for c in cur.children if c.is_named)
        return False

    def is_lock_scope(self, node: Node) -> bool:
        """A ``std::lock_guard`` / ``unique_lock`` declaration holds the mutex to
        the end of its enclosing block, so the *block* is the held region.

        The walker raises ``lock_depth`` for a node's block-typed children only;
        a guard is a sibling statement, not a block-scoped construct with its own
        body, so there is no node whose body is exactly the held region. Reporting
        ``False`` keeps ``blocking_io_under_lock`` at its no-signal default for
        the RAII shape rather than guessing a region — the ceiling here is
        recall. The explicit ``mu.lock()`` acquire/release pair is out of scope
        for the same reason it is in every other dialect.
        """
        return False


DIALECT = CppPerfDialect()
