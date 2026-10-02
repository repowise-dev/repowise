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
    from collections.abc import Iterable, Iterator

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
#
# ``socket`` / ``setsockopt`` / ``getsockopt`` are absent too: they create or
# configure a descriptor in the local kernel and put nothing on the wire, so a
# loop of them is not N round-trips (aria2's per-address connect loop matched).
C_NET_FUNCTIONS: frozenset[str] = frozenset(
    {
        "sendto",
        "sendmsg",
        "recvfrom",
        "recvmsg",
        "getaddrinfo",
        "gethostbyname",
    }
)
# ``getaddrinfo`` with one of these flags only parses a numeric address and
# never asks a name server (``AI_NUMERICHOST``).
RESOLVER_FUNCTIONS: frozenset[str] = frozenset({"getaddrinfo"})
NUMERIC_ONLY_RESOLVER_FLAGS: frozenset[str] = frozenset({"AI_NUMERICHOST"})
# A ``while`` / ``do`` loop whose condition requires one of these errno values
# repeats only when a signal interrupted the call: a retry, not a walk over data.
# Matched as a suffix, so project spellings (``A2_EINTR``, ``WSAEINTR``) count.
SIGNAL_RETRY_ERRNOS: tuple[str, ...] = ("EINTR",)
# Links of lists that hold a handful of candidates for ONE operation (the
# addresses one host name resolved to), walked to try each until one works.
CANDIDATE_LIST_LINKS: frozenset[str] = frozenset({"ai_next"})
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


def _subtree(node: Node | None) -> Iterator[Node]:
    return BasePerfDialect._walk(node) if node is not None else iter(())


def _names_identifier(node: Node | None, suffixes: Iterable[str]) -> bool:
    """True when *node*'s subtree holds an identifier ending in one of *suffixes*."""
    ends = tuple(suffixes)
    return any(
        n.type == "identifier" and (n.text or b"").decode("utf-8", "replace").endswith(ends)
        for n in _subtree(node)
    )


def _conjuncts(node: Node | None) -> list[Node]:
    """The ``&&`` operands of a loop condition, through parentheses and the
    ``condition_clause`` wrapper (one operand when there is no ``&&``)."""
    if node is None:
        return []
    if node.type in ("condition_clause", "parenthesized_expression") and node.named_child_count == 1:
        return _conjuncts(node.named_children[0])
    op = node.child_by_field_name("operator")
    if node.type == "binary_expression" and op is not None and op.type == "&&":
        return _conjuncts(node.child_by_field_name("left")) + _conjuncts(
            node.child_by_field_name("right")
        )
    return [node]


def _is_signal_retry_loop(node: Node) -> bool:
    """``while ((n = f()) == -1 && errno == EINTR);``: every pass past the first
    needs an interrupted call, so the loop retries one call rather than walking
    data. An ``||`` condition can repeat for another reason and does not match."""
    if node.type not in ("while_statement", "do_statement"):
        return False
    return any(
        c.type == "binary_expression"
        and (op := c.child_by_field_name("operator")) is not None
        and op.type == "=="
        and _names_identifier(c, SIGNAL_RETRY_ERRNOS)
        for c in _conjuncts(node.child_by_field_name("condition"))
    )


def _walks_candidate_list(node: Node) -> bool:
    """``for (rp = res; rp; rp = rp->ai_next)``: tries each address one name
    resolved to, a bounded candidate list rather than a data collection."""
    update = node.child_by_field_name("update") if node.type == "for_statement" else None
    return any(
        n.type == "field_identifier" and (n.text or b"").decode() in CANDIDATE_LIST_LINKS
        for n in _subtree(update)
    )


def _assigned_field(node: Node) -> tuple[bytes | None, bytes | None]:
    """``(object, field)`` of an ``obj.field = ...`` assignment, else ``(None, None)``."""
    left = node.child_by_field_name("left") if node.type == "assignment_expression" else None
    if left is None or left.type != "field_expression":
        return None, None
    obj, field = left.child_by_field_name("argument"), left.child_by_field_name("field")
    return (obj.text if obj else None), (field.text if field else None)


def _hints_flags_assignment(call: Node, hints: bytes) -> Node | None:
    """The last ``<hints>.ai_flags = ...`` written before *call* in its function."""
    fn = call.parent
    while fn is not None and fn.type not in ("function_definition", "lambda_expression"):
        fn = fn.parent
    writes = [
        n
        for n in _subtree(fn)
        if n.end_byte <= call.start_byte and _assigned_field(n) == (hints, b"ai_flags")
    ]
    return max(writes, key=lambda n: n.start_byte, default=None)


def _hints_argument(args: Node) -> bytes | None:
    """The name of ``getaddrinfo``'s third argument (``&hints`` -> ``hints``)."""
    named = args.named_children
    hints = named[2] if len(named) >= 3 else None
    if hints is not None and hints.type == "pointer_expression":
        hints = hints.child_by_field_name("argument")
    return hints.text if hints is not None and hints.type == "identifier" else None


def _resolves_numeric_only(call: Node) -> bool:
    """A ``getaddrinfo`` call made with a numeric-only flag, passed directly or
    as the last ``<hints>.ai_flags`` assignment before the call.

    Ceiling: a wrapper that forwards the flag through its own parameter
    (``callGetaddrinfo(..., AI_NUMERICHOST)``) is still read as a lookup,
    since the flag would have to be followed across the call.
    """
    args = call.child_by_field_name("arguments")
    if args is None:
        return False
    if _names_identifier(args, NUMERIC_ONLY_RESOLVER_FLAGS):
        return True
    hints = _hints_argument(args)
    flags = _hints_flags_assignment(call, hints) if hints else None
    return flags is not None and _names_identifier(
        flags.child_by_field_name("right"), NUMERIC_ONLY_RESOLVER_FLAGS
    )


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

    def call_sink_kind(
        self, call: Node, *, awaited: bool, io_names: dict[str, str], has_db_import: bool
    ) -> str | None:
        kind = super().call_sink_kind(
            call, awaited=awaited, io_names=io_names, has_db_import=has_db_import
        )
        if kind == "network" and self.callee_method_name(call) in RESOLVER_FUNCTIONS:
            return None if _resolves_numeric_only(call) else kind
        return kind

    # -- loops ----------------------------------------------------------------

    def is_constant_loop(self, node: Node) -> bool:
        """Not a data-dependent multiplier: ``for (int i = 0; i < 8; i++)`` with
        a literal bound, an ``EINTR`` retry loop, or a walk over the addresses
        one name resolved to. A range-for always iterates data, and any other
        ``while`` / ``do`` bound is opaque."""
        if _is_signal_retry_loop(node) or _walks_candidate_list(node):
            return True
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
