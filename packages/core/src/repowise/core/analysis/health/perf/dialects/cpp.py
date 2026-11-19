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
from .iterables import IterableSources, text_of, upper_bound

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
# loop of them is not N round-trips (aria2's bind and sockopt helpers matched).
# ``socket`` counts again in a function that also connects or moves data.
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
# Next to a POSIX ``socket()`` in the same function these are the POSIX calls,
# so the socket stands for a connection that carries traffic.
SOCKET_OPENERS: frozenset[str] = frozenset({"socket"})
SOCKET_TRAFFIC_CALLS: frozenset[str] = frozenset(
    {"connect", "send", "recv", "sendto", "sendmsg", "recvfrom", "recvmsg"}
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
    if _operator(node) == "&&":
        return _conjuncts(node.child_by_field_name("left")) + _conjuncts(
            node.child_by_field_name("right")
        )
    return [node]


def _operator(node: Node) -> str | None:
    """The operator token of a binary / assignment / unary expression."""
    op = node.child_by_field_name("operator")
    return op.type if op is not None else None


def _tests_error_return(node: Node) -> bool:
    """``(r = f()) == -1`` / ``n < 0`` / ``rc != 0``: a comparison against a
    number literal, the shape of a call's error-return test."""
    return node.type == "binary_expression" and any(
        side is not None and side.type == "number_literal"
        for side in (node.child_by_field_name("left"), node.child_by_field_name("right"))
    )


def _is_signal_retry_loop(node: Node) -> bool:
    """``while ((n = f()) == -1 && errno == EINTR);``: every pass past the first
    needs a failed AND interrupted call, so the loop retries one call rather
    than walking data.

    Every conjunct must be either the ``errno == EINTR`` test or an error-return
    test, so ``while (i < n && errno == EINTR)`` (a counter can keep it going)
    and any ``||`` condition do not match.
    """
    if node.type not in ("while_statement", "do_statement"):
        return False
    conjuncts = _conjuncts(node.child_by_field_name("condition"))
    retry = [
        c for c in conjuncts if _operator(c) == "==" and _names_identifier(c, SIGNAL_RETRY_ERRNOS)
    ]
    others = [c for c in conjuncts if c not in retry]
    return bool(retry) and bool(others) and all(_tests_error_return(c) for c in others)


def _steps_through_candidates(node: Node | None) -> bool:
    """*node* is or holds ``rp->ai_next`` (a candidate-list link)."""
    return any(
        n.type == "field_identifier" and (n.text or b"").decode() in CANDIDATE_LIST_LINKS
        for n in _subtree(node)
    )


def _walks_candidate_list(node: Node) -> bool:
    """``for (rp = res; rp; rp = rp->ai_next)`` or ``while (rp) { ...; rp =
    rp->ai_next; }``: tries each address one name resolved to, a bounded
    candidate list rather than a data collection."""
    if node.type == "for_statement":
        return _steps_through_candidates(node.child_by_field_name("update"))
    body = node.child_by_field_name("body") if node.type == "while_statement" else None
    return body is not None and any(
        stmt.type == "expression_statement"
        and stmt.named_child_count == 1
        and stmt.named_children[0].type == "assignment_expression"
        and _steps_through_candidates(stmt.named_children[0].child_by_field_name("right"))
        for stmt in body.named_children
    )


def _enclosing_function(node: Node) -> Node | None:
    fn = node.parent
    while fn is not None and fn.type not in ("function_definition", "lambda_expression"):
        fn = fn.parent
    return fn


def _callee_name(call: Node) -> str:
    """Last ``::`` segment of a call's callee text (``::connect`` -> ``connect``)."""
    callee = call.child_by_field_name("function")
    return (callee.text or b"").decode("utf-8", "replace").split("::")[-1] if callee else ""


def _calls_named(fn: Node | None, names: frozenset[str]) -> bool:
    """*fn* makes a call whose last callee segment is one of *names*."""
    return any(n.type == "call_expression" and _callee_name(n) in names for n in _subtree(fn))


def _pointee(node: Node) -> Node:
    """``&hints`` -> ``hints``; any other node unchanged."""
    inner = node.child_by_field_name("argument") if node.type == "pointer_expression" else None
    return inner or node


def _field_write(node: Node) -> tuple[bytes | None, bytes | None]:
    """``(object, field)`` an assignment writes (``(b"hints", b"ai_flags")``), or
    ``(object, None)`` for a whole-object write."""
    left = node.child_by_field_name("left") if node.type == "assignment_expression" else None
    if left is None:
        return None, None
    if left.type != "field_expression":
        return left.text, None
    obj, field = left.child_by_field_name("argument"), left.child_by_field_name("field")
    return (obj.text if obj else None), (field.text if field else None)


def _touches_hints(node: Node, hints: bytes) -> str | None:
    """How *node* writes the hints struct: ``"flags"`` for ``hints.ai_flags op
    ...``, ``"reset"`` for ``hints = ...`` or a call handed ``&hints`` /
    ``hints`` (``memset`` / ``bzero``), else ``None``."""
    obj, field = _field_write(node)
    if obj == hints:
        return {None: "reset", b"ai_flags": "flags"}.get(field)
    args = node.child_by_field_name("arguments") if node.type == "call_expression" else None
    passed = args.named_children if args is not None else ()
    return "reset" if any(_pointee(a).text == hints for a in passed) else None


def _or_chain_top(node: Node) -> Node:
    """The highest ancestor reached from *node* through parentheses and ``|``."""
    cur = node
    while cur.parent is not None and (
        cur.parent.type == "parenthesized_expression" or _operator(cur.parent) == "|"
    ):
        cur = cur.parent
    return cur


def _sets_flag_unmasked(expr: Node | None) -> bool:
    """*expr* ORs a numeric-only flag in: the flag sits under nothing but
    ``|`` and parentheses (``~AI_NUMERICHOST`` / ``f & AI_NUMERICHOST`` do not count)."""
    return any(
        n.type == "identifier"
        and (n.text or b"").decode() in NUMERIC_ONLY_RESOLVER_FLAGS
        and _or_chain_top(n) == expr
        for n in _subtree(expr)
    )


def _unconditional_before(write: Node, call: Node) -> bool:
    """*write* is a statement of a block that also holds *call*, so it runs on
    every path that reaches the call (not under an ``if`` or ``?:``)."""
    stmt = write.parent
    block = stmt.parent if stmt is not None and stmt.type == "expression_statement" else None
    return (
        block is not None
        and block.type == "compound_statement"
        and block.start_byte <= call.start_byte < block.end_byte
    )


def _hints_argument(args: Node) -> bytes | None:
    """The name of ``getaddrinfo``'s third argument (``&hints`` -> ``hints``)."""
    named = args.named_children
    hints = _pointee(named[2]) if len(named) >= 3 else None
    return hints.text if hints is not None and hints.type == "identifier" else None


def _last_hints_write(call: Node, hints: bytes) -> Node | None:
    """The last statement before *call* in its function that writes *hints*."""
    writes = [
        n
        for n in _subtree(_enclosing_function(call))
        if n.end_byte <= call.start_byte and _touches_hints(n, hints)
    ]
    return max(writes, key=lambda n: n.start_byte, default=None)


def _resolves_numeric_only(call: Node) -> bool:
    """A ``getaddrinfo`` call whose hints were last written by an unconditional
    ``hints.ai_flags = AI_NUMERICHOST`` (or ``|=`` / ``x | AI_NUMERICHOST``).

    Any later reset of the struct (``memset(&hints, ...)``, ``hints = {}``), a
    masking write (``&= ~AI_NUMERICHOST``) or a write on one branch only keeps
    the call a lookup. Ceiling: a wrapper that forwards the flag through its own
    parameter (``callGetaddrinfo(..., AI_NUMERICHOST)``) is still read as a
    lookup, since the flag would have to be followed across the call.
    """
    args = call.child_by_field_name("arguments")
    hints = _hints_argument(args) if args is not None else None
    last = _last_hints_write(call, hints) if hints else None
    if last is None or _touches_hints(last, hints) != "flags":
        return False
    return (
        _operator(last) in ("=", "|=")
        and _sets_flag_unmasked(last.child_by_field_name("right"))
        and _unconditional_before(last, call)
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


_CV_WAITS: frozenset[bytes] = frozenset({b"wait", b"wait_for", b"wait_until"})
_LOOP_KINDS: frozenset[str] = frozenset(
    {"for_statement", "for_range_loop", "while_statement", "do_statement"}
)


def _waits_on_guard(decl: Node) -> bool:
    """``std::unique_lock lk(mu);`` handed to ``cv.wait(lk)`` in the same loop:
    a condition-variable wait needs the lock taken there, so it cannot be hoisted."""
    guard = next(
        (
            c.child_by_field_name("declarator")
            for c in decl.named_children
            if c.type in ("init_declarator", "function_declarator")
        ),
        None,
    )
    if guard is None or guard.type != "identifier":
        return False
    loop = decl.parent
    while loop is not None and loop.type not in _LOOP_KINDS:
        if loop.type in _CPP_FN_KINDS:
            return False
        loop = loop.parent
    return any(_is_wait_on(n, guard.text) for n in _subtree(loop))


def _is_wait_on(node: Node, guard: bytes | None) -> bool:
    """``cv.wait(guard, ...)`` / ``cv_->wait_for(guard, ...)``."""
    if node.type != "call_expression":
        return False
    fn, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
    if fn is None or fn.type != "field_expression":
        return False
    if args is None or not args.named_children:
        return False
    field = fn.child_by_field_name("field")
    return field is not None and field.text in _CV_WAITS and args.named_children[0].text == guard


_CPP_FN_KINDS: frozenset[str] = frozenset({"function_definition", "lambda_expression"})
# ``while ((row = mysql_fetch_row(res)))``: a row cursor over the result set it is passed.
_ROW_CURSORS: frozenset[str] = frozenset({"mysql_fetch_row", "PQgetResult"})
_DECLARATORS: frozenset[str] = frozenset(
    {"array_declarator", "reference_declarator", "pointer_declarator", "init_declarator"}
)


def _declared_name(node: Node | None) -> bytes | None:
    """``x`` of ``x`` / ``x[4]`` / ``&x`` / ``*x`` / ``x = v`` (a member's ``x`` too)."""
    while node is not None and node.type in _DECLARATORS:
        node = node.child_by_field_name("declarator") or next(
            (c for c in node.named_children if c.type == "identifier"), None
        )
    named = node is not None and node.type in ("identifier", "field_identifier")
    return node.text if named else None


def _std_array(node: Node | None) -> bool:
    """``std::array<T, 4>`` / ``array<T, 4>``: a size fixed in the type."""
    if node is not None and node.type == "qualified_identifier":
        if text_of(node.child_by_field_name("scope")) != "std":
            return False
        node = node.child_by_field_name("name")
    if node is None or node.type != "template_type":
        return False
    args = node.child_by_field_name("arguments")
    if args is None or text_of(node.child_by_field_name("name")) != "array":
        return False
    return any(arg.type == "number_literal" for arg in args.named_children)


def _cpp_bindings(node: Node) -> list[tuple[bytes, Node]]:
    """What *node* binds: ``T x = v`` / ``T x{a, b}`` (the value), ``T x[16]``
    (the declarator), ``std::array<T, 4> x`` (the type), ``x = v``, or
    ``sqlite3_prepare_v2(db, sql, n, &x, ..)`` (the call). A null pointer binds
    nothing: the statement that fills it does."""
    if node.type == "declaration":
        return _declared_values(node)
    if node.type == "assignment_expression":
        left, op = node.child_by_field_name("left"), node.child_by_field_name("operator")
        right = node.child_by_field_name("right")
        plain = left is not None and op is not None and op.type == "=" and right is not None
        return [(left.text, right)] if plain and left.type == "identifier" and left.text else []
    return _prepared_into(node) if node.type == "call_expression" else []


def _prepared_into(call: Node) -> list[tuple[bytes, Node]]:
    """``stmt`` of ``sqlite3_prepare_v2(db, sql, n, &stmt, ..)``, bound to the call."""
    args = call.child_by_field_name("arguments")
    if args is None or not _callee_name(call).startswith("sqlite3_prepare"):
        return []
    out = [a.child_by_field_name("argument") for a in args.named_children]
    return [(a.text, call) for a in out if a is not None and a.type == "identifier" and a.text]


def _declared_values(node: Node) -> list[tuple[bytes, Node]]:
    found: list[tuple[bytes, Node]] = []
    ty = node.child_by_field_name("type")
    for decl in node.children_by_field_name("declarator"):
        name = _declared_name(decl)
        if name is None:
            continue
        if decl.type == "init_declarator":
            value = decl.child_by_field_name("value")
            if value is not None and value.type != "null" and value.text != b"NULL":
                found.append((name, value))
        elif decl.type == "array_declarator":
            found.append((name, decl))
        elif _std_array(ty):
            found.append((name, ty))
    return found


def _fixed_decl(node: Node) -> bool:
    """A file-level ``const`` / ``constexpr`` declaration, or an array of one."""
    qualified = any(c.type == "type_qualifier" for c in node.children)
    return qualified or any(_declared_array(d) for d in node.children_by_field_name("declarator"))


def _declared_array(node: Node | None) -> bool:
    while node is not None and node.type in ("init_declarator", "reference_declarator", "pointer_declarator"):
        node = node.child_by_field_name("declarator")
    return node is not None and node.type == "array_declarator"


def _end_of(cond: Node | None) -> Node | None:
    """``xs`` of an iterator loop's ``it != xs.end()``."""
    if cond is None or cond.type != "binary_expression":
        return None
    op, right = cond.child_by_field_name("operator"), cond.child_by_field_name("right")
    if op is None or op.type != "!=" or right is None or right.type != "call_expression":
        return None
    fn = right.child_by_field_name("function")
    if fn is None or fn.type != "field_expression":
        return None
    field = text_of(fn.child_by_field_name("field"))
    return fn.child_by_field_name("argument") if field in ("end", "cend") else None


def _row_test(cond: Node | None) -> Node | None:
    """The call a ``while`` tests: ``f(..)``, ``f(..) == ROW``, ``(row = f(..))``."""
    while cond is not None and cond.type in ("condition_clause", "parenthesized_expression"):
        inner = cond.child_by_field_name("value")
        cond = inner if inner is not None else next(iter(cond.named_children), None)
    if cond is not None and cond.type == "binary_expression":
        cond = cond.child_by_field_name("left")
    if cond is not None and cond.type == "assignment_expression":
        cond = cond.child_by_field_name("right")
    return cond if cond is not None and cond.type == "call_expression" else None


class CppPerfDialect(IterableSources):
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
        method = self.callee_method_name(call)
        if kind == "network" and method in RESOLVER_FUNCTIONS:
            return None if _resolves_numeric_only(call) else kind
        if kind is None and method in SOCKET_OPENERS and self._opens_traffic_socket(call):
            return "network"
        return kind

    def _opens_traffic_socket(self, call: Node) -> bool:
        """An unqualified ``socket()`` in a function that also connects or moves data."""
        unqualified = self.callee_method_name(call) == self.callee_root_name(call)
        return (
            unqualified
            and not self.callee_is_attribute(call)
            and _calls_named(_enclosing_function(call), SOCKET_TRAFFIC_CALLS)
        )

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
            return None if _waits_on_guard(node) else "lock_in_loop"
        return None

    # -- what a loop walks (perf/dialects/iterables.py) ---------------------------

    function_kinds = _CPP_FN_KINDS
    file_container_kinds = frozenset({"namespace_definition"})
    member_scope_kinds = frozenset({"class_specifier", "struct_specifier", "union_specifier"})

    int_kinds = frozenset({"number_literal"})
    wrapper_kinds = frozenset({"parenthesized_expression", "pointer_expression"})
    size_names = frozenset({"size", "length", "ssize", "ABSL_ARRAYSIZE", "arraysize"})
    # ``while (rs->next())`` (a SQL connector's result set).
    cursor_advances = frozenset({"next"})
    listings = frozenset({"directory_iterator", "recursive_directory_iterator"})
    recursive_listings = frozenset({"recursive_directory_iterator"})
    streams = frozenset({"getline"})
    member_kinds = frozenset({"field_expression"})
    change_kinds = frozenset({"assignment_expression", "update_expression"})
    grow_methods = frozenset(
        {
            "push_back", "emplace_back", "emplace", "push_front", "emplace_front", "insert",
            "resize", "assign", "append",
        }
    )
    conditional_kinds = frozenset(
        {
            "if_statement", "else_clause", "switch_statement", "case_statement", "for_statement",
            "for_range_loop", "while_statement", "do_statement", "try_statement", "catch_clause",
            "conditional_expression",
        }
    )

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        if loop.type == "for_range_loop":
            return "each", loop.child_by_field_name("right")
        cond = loop.child_by_field_name("condition")
        end = _end_of(cond)
        if end is not None:
            return "each", end
        if loop.type == "for_statement":
            return "counted", upper_bound(cond)
        call = _row_test(cond)
        parts = self.call_parts(call) if call is not None else None
        if parts is None:
            return "each", None
        method, _receiver, args = parts
        if method in _ROW_CURSORS:
            return "each", args[0] if args else None
        # ``sqlite3_step(stmt)`` is the read itself; ``getline(in, s)`` reads a stream.
        if method in DB_FUNCTIONS or method in self.streams:
            return "each", call
        return "cursor", call

    def is_literal(self, node: Node) -> bool:
        """``{a, b}``, ``T x[16]`` and ``std::array<T, 4>``; not ``{args...}``."""
        if node.type == "initializer_list":
            entries = [c for c in node.named_children if c.type != "comment"]
            return bool(entries) and not any(c.type == "parameter_pack_expansion" for c in entries)
        if node.type == "array_declarator":
            size = node.child_by_field_name("size")
            return size is not None and size.type in self.int_kinds
        return _std_array(node)

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        arg_nodes = [c for c in args.named_children if c.type != "comment"] if args else []
        if fn is not None and fn.type == "field_expression":
            field, obj = fn.child_by_field_name("field"), fn.child_by_field_name("argument")
            return text_of(field), obj, arg_nodes
        # ``std::size(xs)``: a namespace is not a receiver.
        while fn is not None and fn.type in ("qualified_identifier", "template_function"):
            fn = fn.child_by_field_name("name")
        return text_of(fn), None, arg_nodes

    def member_parts(self, node: Node) -> tuple[str, Node] | None:
        obj = node.child_by_field_name("argument") if node.type == "field_expression" else None
        return (text_of(node.child_by_field_name("field")), obj) if obj is not None else None

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        return _cpp_bindings(node)

    def declared_names(self, node: Node) -> list[bytes]:
        """Every name a declaration, parameter or range-for declares, initialised or not."""
        if node.type in ("declaration", "parameter_declaration", "optional_parameter_declaration"):
            names = (_declared_name(d) for d in node.children_by_field_name("declarator"))
            return [name for name in names if name]
        if node.type == "for_range_loop":
            decl = node.child_by_field_name("declarator")
            return [n.text for n in self._walk(decl) if n.type == "identifier" and n.text] if decl else []
        return []

    def escaped_names(self, node: Node) -> list[bytes]:
        """``v`` of ``fill(v)`` / ``fill(&v)``: the callee may take it by reference."""
        parts = self.call_parts(node)
        names: list[bytes] = []
        for arg in parts[2] if parts is not None else []:
            if arg.type == "pointer_expression":
                arg = arg.child_by_field_name("argument") or arg
            if arg.type == "identifier" and arg.text:
                names.append(arg.text)
        return names

    def file_bindings_of(self, item: Node) -> list[tuple[bytes, Node]]:
        """A namespace-level ``const`` / ``constexpr`` or array."""
        return _declared_values(item) if item.type == "declaration" and _fixed_decl(item) else []

    def member_names(self, owner: Node) -> set[bytes]:
        """Fields a method defined in the class body reads bare. Ceiling: a method
        defined out of line (``void A::f()``) is not inside its class here."""
        body = owner.child_by_field_name("body")
        fields = [c for c in body.named_children if c.type == "field_declaration"] if body else []
        return {
            name
            for member in fields
            for decl in member.children_by_field_name("declarator")
            if (name := _declared_name(decl))
        }

    def param_names(self, loop: Node) -> set[bytes]:
        scope = self._scope(loop)
        decl = scope.child_by_field_name("declarator") if scope is not None else None
        while decl is not None and decl.child_by_field_name("parameters") is None:
            decl = decl.child_by_field_name("declarator")
        params = decl.child_by_field_name("parameters") if decl is not None else None
        return {
            name
            for param in (params.named_children if params is not None else ())
            if (name := _declared_name(param.child_by_field_name("declarator")))
        }

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
