"""Performance-risk detection (the ``performance`` health dimension).

io_in_loop / string_concat_in_loop / blocking_sync_in_async and the Phase-7
loop-level / centrality-gated markers.

One whole-tree pass, kept apart from the walker's shared ``file_scan`` because
it carries the per-node context the perf signal needs down the tree: loop depth,
in-async, lock depth, and the enclosing function name. Two non-negotiable refinements (Phase-0 gate: they
took precision from 49% to 79%) are baked in:
  1. Loop-BODY scoping — only calls under a loop node's ``body`` field run
     per-iteration; a call in the ``for x in <iterable>`` header runs once.
  2. Constant-bound-loop skip — ``for _ in range(<int literals>)`` and loops
     over literal / ALL_CAPS-named-constant collections are not data-dependent.

The pass is language-agnostic: it looks up ``PERF_DIALECTS[language]`` and
drives the DFS off the dialect's predicates, early-outing when none is
registered.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from ..perf.dialects import PERF_DIALECTS
from ..perf.dialects.base import BasePerfDialect as BasePerfDialectClass
from ..perf.loop_facts import BatchForm, LoopFacts, LoopSource, combined_magnitude
from ..perf.table_scale import small_table_candidate
from .ast_utils import (
    _dart_signature_sibling,
    _find_function_entry_name,
    _find_name,
    _node_text,
    is_function_node,
)
from .languages import LanguageNodeMap
from .models import PerfFnFacts, PerfHit

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..perf.dialects.base import BasePerfDialect

# Marker kinds emitted through each dialect hook. The walker consults a
# dialect's ``markers`` set against these to decide whether to invoke the hook
# at all, so a dialect that lists none of a hook's kinds never pays for it.
_LOOP_CALL_MARKER_KINDS = frozenset(
    {
        "regex_compile_in_loop",
        "resource_construction_in_loop",
        "lock_in_loop",
        "membership_test_against_list_in_loop",
        # Phase 7d — language-specific call markers.
        "list_insert_zero_in_loop",
        "pd_concat_in_loop",
        "json_parse_in_loop",
    }
)
_LOOP_STMT_MARKER_KINDS = frozenset(
    {
        "defer_in_loop",
        "resource_construction_in_loop",
        "lock_in_loop",
        "membership_test_against_list_in_loop",
        # Phase 7d — language-specific statement markers.
        "goroutine_in_unbounded_loop",
        # Scala ``"...".r`` is a bare ``field_expression`` (not a call), so its
        # regex-recompile form arrives through the statement hook.
        "regex_compile_in_loop",
    }
)
# Markers for a call that is its OWN iteration construct (``.reduce``), a perf
# smell at any loop depth — emitted via ``dialect.bare_call_marker``.
_BARE_CALL_MARKER_KINDS = frozenset({"array_spread_in_reduce"})
# Markers fired on a loop whose ITERABLE is itself a slow call (``df.iterrows()``)
# — the call lives in the loop header, so the body markers miss it. Emitted via
# ``dialect.loop_iterable_call_marker`` against the loop node.
_LOOP_ITERABLE_CALL_MARKER_KINDS = frozenset({"pandas_iterrows_in_loop"})

# Boundary kinds that are *unambiguously blocking* when called synchronously, so
# a loop_depth-0 occurrence in a hot function is a ``hot_path_sync_io`` candidate.
# Limited to ``subprocess`` + ``filesystem`` on purpose:
#   * ``db`` — in an async codebase DB access is awaited (non-blocking), and the
#     un-awaited DB calls a static pass sees are almost all result *materializers*
#     (``result.scalars().all()`` / ``.scalar_one()``) on an already-awaited
#     result, not a round-trip (a probe-confirmed FP generator).
#   * ``network`` — async HTTP is awaited too; the walker's ``awaited`` test only
#     inspects the immediate parent, so a wrapped/chained await
#     (``(await client.GetAsync(u)).X``) can read as non-awaited and FP. Every
#     hot-path TP on the OSS corpus was subprocess/fs (0 network), so excluding
#     it costs no measured precision and removes that FP class. (A wrapper-aware
#     ``awaited`` test could re-admit sync ``requests`` — tracked as a follow-up.)
# subprocess (``subprocess.run`` / ``os.system``) and filesystem (bare ``open``)
# are synchronous by construction, with no await semantics to misread.
_HOT_PATH_SINK_KINDS = frozenset({"subprocess", "filesystem"})

# Block node kinds that form the *body* of a lock construct (C# ``lock (x) {…}``
# / Java ``synchronized (x) {…}`` / Ruby ``mutex.synchronize do … end``). Only
# the body runs with the lock held, so ``lock_depth`` is raised for the body
# child only — a sink in the lock-object expression
# (``synchronized(repo.find(id)){…}``) runs BEFORE the lock is taken.
_LOCK_BODY_KINDS = frozenset({"block", "statement_block", "compound_statement", "do_block"})

# A whole condition that is one null test, and the name it tests: ``x == null``,
# ``this.x.get() == null``, ``o.x is null`` (the last segment, ``.get()`` stripped).
_NULL_GUARD = re.compile(
    r"(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)(?:\.get\(\))?"
    r"\s*(?:===?\s*(?:null|nil|None)|is\s+null)"
)
_IF_KINDS = frozenset({"if_statement", "if_expression"})
# ``x = ...`` / ``x ??= ...`` / ``x.set(...)``: the held body stores the guarded name.
_ASSIGNS = r"\b{}\s*(?:=(?!=)|\?\?=|\.set\(|\.compareAndSet\()"


def _null_guarded_name(node: Node | None) -> str | None:
    """The name *node* tests when it is exactly ``if (x == null) {...}``, with no ``else``."""
    if node is None or node.type not in _IF_KINDS or node.child_by_field_name("alternative"):
        return None
    cond = node.child_by_field_name("condition")
    text = (cond.text if cond is not None else b"").decode("utf-8", "replace")
    guard = _NULL_GUARD.fullmatch(text.strip("() \t\r\n"))
    return guard.group(1) if guard is not None else None


def _sole_statement(body: Node) -> Node | None:
    """The one statement a held body runs, ignoring comments and a trailing ``return``."""
    stmts = [c for c in body.children if c.is_named and "comment" not in c.type]
    if len(stmts) == 2 and stmts[1].type == "return_statement":
        stmts = stmts[:1]
    return stmts[0] if len(stmts) == 1 else None


def _is_coalescing_assignment(stmt: Node | None) -> bool:
    """``x ??= Load();``: assigns only when ``x`` is still null."""
    if stmt is None or stmt.type != "expression_statement":
        return False
    return b"??=" in (stmt.text or b"")


def _enclosing_statement(lock: Node) -> Node | None:
    """The statement around *lock*, looking through the block it sits in."""
    outer = lock.parent
    if outer is not None and outer.type in _LOCK_BODY_KINDS:
        outer = outer.parent
    return outer


def _is_memoizing_lock(lock: Node) -> bool:
    """The lock only makes a one-time initialisation thread-safe.

    The held body assigns the name a null guard tests, where the guard either
    wraps the lock (``if (x == null) { synchronized (m) { x = ... } }``) or is
    the whole held body (double-checked locking); or the whole body is
    ``x ??= Load()``. A trailing ``return`` is allowed. The I/O then runs once,
    and moving it out of the lock would run it more than once, so such a lock
    is not a ``blocking_io_under_lock`` region.
    """
    body = next((c for c in lock.children if c.type in _LOCK_BODY_KINDS), None)
    if body is None:
        return False
    only = _sole_statement(body)
    if _is_coalescing_assignment(only):
        return True
    held = (body.text or b"").decode("utf-8", "replace")
    for guard in (only, _enclosing_statement(lock)):
        name = _null_guarded_name(guard)
        if name and re.search(_ASSIGNS.format(re.escape(name)), held):
            return True
    return False


def _perf_func_name(node: Node) -> str | None:
    if node.type == "function_body":
        # Dart: the name lives on the preceding signature sibling.
        sig = _dart_signature_sibling(node)
        if sig is not None:
            name = _find_name(sig)
            return name if name and name != "<anonymous>" else None
    nm = node.child_by_field_name("name")
    if nm is not None and nm.text:
        return nm.text.decode("utf-8", "replace")
    # C / C++: the name is nested in a ``declarator`` chain, not a ``name``
    # field. Reuse the complexity walker's probe rather than re-deriving it —
    # it already handles that chain (and every other irregular shape).
    name = _find_name(node)
    return name if name != "<anonymous>" else None


def _name_lambda_hits(hits: list[PerfHit], spans: list[tuple[int, int, str]]) -> list[PerfHit]:
    """Name a hit with no enclosing named function after the lambda holding it.

    The innermost span wins, and the name is the one the complexity walker gives
    that lambda (its binding, ``<callee> callback``, or ``<anonymous@line>``), so
    the two passes agree. A hit outside every lambda is top-level code and keeps
    ``None``. Only the hit is renamed: the per-function facts stay keyed to
    module scope, where the cross-function bridge resolves them.
    """
    if not spans:
        return hits
    named: list[PerfHit] = []
    for hit in hits:
        if hit.function is None:
            inside = [span for span in spans if span[0] <= hit.line <= span[1]]
            if inside:
                innermost = min(inside, key=lambda span: span[1] - span[0])
                hit = replace(hit, function=innermost[2])
        named.append(hit)
    return named


def _enclosing_loop_iterables(
    node: Node,
    dialect: BasePerfDialect,
    loop_kinds: frozenset[str],
    fn_kinds: frozenset[str],
    block_loops: bool = False,
) -> set[str]:
    """Names of the collections every enclosing loop (up to the function bound)
    iterates over — the lookup the same-collection ``nested_loop_quadratic``
    shape gate compares the inner loop's iterable against. When the dialect
    recognises block-iteration calls (*block_loops*), those count as enclosing
    loops too (Ruby ``items.each do … end``)."""
    names: set[str] = set()
    cur = node.parent
    for _ in range(64):
        if cur is None or cur.type in fn_kinds:
            break
        if cur.type in loop_kinds or (block_loops and dialect.block_loop_body(cur) is not None):
            nm = dialect.loop_iterable_name(cur)
            if nm:
                names.add(nm)
        cur = cur.parent
    return names


def _is_lock_acquire_spin(
    node: Node,
    func: str | None,
    dialect: BasePerfDialect,
    loop_kinds: frozenset[str],
    fn_kinds: frozenset[str],
) -> bool:
    """Is *node* inside the unbounded retry loop of a lock-acquiring function?

    Only the nearest enclosing loop counts: a per-item loop nested in (or
    instead of) the spin loop still takes a lock per iteration.
    """
    cur = node.parent
    while cur is not None and cur.type not in fn_kinds:
        if cur.type in loop_kinds and cur.is_named:
            return dialect.is_lock_acquire_spin(func, cur)
        cur = cur.parent
    return False


def _enclosing_loops(
    node: Node,
    dialect: BasePerfDialect,
    loop_kinds: frozenset[str],
    fn_kinds: frozenset[str],
    block_loops: bool = False,
) -> list[Node]:
    """The data-dependent loops around *node* in its function, innermost first;
    walked up only for hits. A loop whose iterable holds *node* is not one. With
    *block_loops*, a block-iteration call (Ruby ``items.each do``) whose block
    holds *node* is one."""
    loops: list[Node] = []
    cur = node.parent
    for _ in range(64):
        if cur is None or cur.type in fn_kinds:
            break
        if block_loops and not dialect.is_constant_loop(cur):
            block = dialect.block_loop_body(cur)
            if block is not None and block.start_byte <= node.start_byte < block.end_byte:
                loops.append(cur)
        if cur.type in loop_kinds and cur.is_named and not dialect.is_constant_loop(cur):
            iterable = dialect.iterable_node(cur)
            # ``for row in result.all():`` runs ``.all()`` once per pass of the loop
            # around it, so the for loop itself is not what repeats it.
            if iterable is None or not (
                iterable.start_byte <= node.start_byte and node.end_byte <= iterable.end_byte
            ):
                loops.append(cur)
        cur = cur.parent
    return loops


# Loop-header fields that bind the element a for-each loop yields, across the
# grammars (``left`` Python/JS/C#, ``name`` Java, ``pattern`` Rust/Ruby,
# ``declarator`` C++ range-for), and the init fields of a counted loop, which
# bind its index only when they declare (``for (int i = 0; ...)``, not
# ``for (p = head; ...)``, which walks a list it was handed).
_ELEMENT_FIELDS = ("left", "name", "pattern", "declarator")
_INIT_FIELDS = ("initializer", "init")
# Go keeps both shapes in a child clause; Kotlin declares the element unfielded.
_LOOP_CLAUSE_KINDS = frozenset({"range_clause", "for_clause"})


def _identifiers(node: Node) -> set[bytes]:
    """Every identifier under *node*, type names excluded."""
    names: set[bytes] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type.endswith("identifier") and cur.type != "type_identifier" and cur.text:
            names.add(cur.text)
        stack.extend(cur.children)
    return names


def _bound_in(part: Node) -> set[bytes]:
    """Names one loop-header part binds through an element field or a declaring init field."""
    nodes = [part.child_by_field_name(field) for field in _ELEMENT_FIELDS]
    nodes += [
        node
        for field in _INIT_FIELDS
        if (node := part.child_by_field_name(field)) is not None and "declaration" in node.type
    ]
    return set().union(*(_identifiers(node) for node in nodes if node is not None))


def _loop_bound_names(loop: Node) -> set[bytes]:
    """The element or index names *loop*'s header binds; empty for a ``while`` loop."""
    parts = [loop, *(c for c in loop.children if c.type in _LOOP_CLAUSE_KINDS)]
    unfielded = [c for c in loop.children if c.type == "variable_declaration"]
    return set().union(*(_bound_in(p) for p in parts), *(_identifiers(c) for c in unfielded))


def _loop_key_feeds(call: Node, loop: Node) -> bool:
    """The loop's element or index appears in *call*, which makes the call per-key.

    A retry or fallback loop (``while (true)``, ``while (tries < max)``), a
    partial-write loop and a walk down a list handed in bind no element, so
    nothing per-key reaches the call and there is no set of keys to batch. A key
    that reaches the call only through a derived local is not counted (recall
    ceiling).
    """
    return bool(_loop_bound_names(loop) & _identifiers(call))


_SINK_CALL_MAX = 60


_STRING_QUOTES = frozenset("'\"`")


def _sink_call_text(call: Node, method: str, dialect: BasePerfDialect) -> str:
    """The callee as written with its arguments dropped, else its method name.

    ``session.get`` stays as is and ``conn.execute(sql).fetchone`` reads
    ``conn.execute().fetchone``. A callee that opens with a parenthesis, runs
    long or holds a string literal (its quotes defeat the paren scan) gives the
    method only, as does a dialect with no callee text.
    """
    text = dialect.callee_text(call)
    if len(text) > 4 * _SINK_CALL_MAX or not _STRING_QUOTES.isdisjoint(text):
        return method
    callee = _strip_call_args(text)
    return callee if _quotable(callee) else method


def _strip_call_args(text: str) -> str:
    """``conn.execute(sql).fetchone`` -> ``conn.execute().fetchone``, whitespace dropped."""
    if "(" not in text:
        return "".join(text.split())
    kept: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            kept.append("()" if depth == 0 else "")
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif depth == 0 and not char.isspace():
            kept.append(char)
    return "".join(kept)


_MEMBERSHIP = "membership_test_against_list_in_loop"
# A name a plan step can quote: ``out``, ``self.buf``, ``@buf``, ``$acc``.
_SUBJECT_NAME = re.compile(r"[@$]{0,2}[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*")


def _subject_text(node: Node | None) -> str:
    """*node* as written when it is a plain name or member path, else empty."""
    text = "".join(_node_text(node).split()) if node is not None else ""
    return text if len(text) <= _SINK_CALL_MAX and _SUBJECT_NAME.fullmatch(text) else ""


def _concat_target(node: Node) -> str:
    """The string a ``+=`` statement grows: its left side."""
    left = node.child_by_field_name("left")
    return _subject_text(left if left is not None else next(iter(node.named_children), None))


def _quotable(callee: str) -> bool:
    return bool(callee) and not callee.startswith("(") and len(callee) <= _SINK_CALL_MAX


def _quotable_chain(text: str) -> bool:
    """A chain's text names its links only when it kept the callee: a chain
    that opens with a parenthesis quotes as its last method alone."""
    return "." in text


def _receiver_chain_calls(
    call: Node, call_kinds: frozenset[str], access_kinds: frozenset[str]
) -> Iterator[Node]:
    """The calls *call* is chained onto, nearest first.

    ``(await session.execute(q)).scalars().all()`` is the ``.all()`` call on the
    ``.scalars()`` call on the ``session.execute`` call: each receiver is the
    first named child of the node above it, through member access,
    parentheses and ``await``. An argument is never on that path, so a call
    passed to a chained call is not one of its links.
    """
    node = call.named_children[0] if call.named_children else None
    while node is not None:
        t = node.type
        if t in call_kinds:
            yield node
        elif not (t in access_kinds or "parenthesized" in t or "await" in t):
            return
        node = node.named_children[0] if node.named_children else None


def _key_unused(hit: PerfHit) -> bool:
    return hit.loop is not None and hit.loop.key_unused


def _without_unused_key(facts: LoopFacts | None) -> LoopFacts | None:
    """Clear ``key_unused``; drop the line when that leaves the default facts.

    ``loop_facts`` returns ``None`` for an all-default ``LoopFacts``. A keyed
    call whose own facts are that default still has to clear a key-free sibling
    on the same line, and the line must not keep a default record afterwards.
    """
    if facts is None:
        return None
    if facts.key_unused:
        facts = replace(facts, key_unused=False)
    return None if facts == LoopFacts() else facts


def _call_line_facts(
    prev: LoopFacts | None,
    facts: LoopFacts | None,
    *,
    this_keyed: bool,
    line_keyed: bool,
) -> LoopFacts | None:
    """One ``LoopFacts`` per call line. Key-free only when every call on it is.

    The same-line sink dedupe keeps the hit the loop key reaches. Here that
    rule applies to helper calls: a keyed call (``_loop_key_feeds``) clears
    ``key_unused`` whichever call is visited first. When the keyed call's own
    facts are all default, the clear is applied to the facts already stored
    for the line.
    """
    if this_keyed and facts is not None:
        return facts
    if line_keyed:
        # A keyed call already stored non-default facts with the key in use.
        if not this_keyed and prev is not None and not prev.key_unused:
            return prev
        return _without_unused_key(facts if facts is not None else prev)
    return facts if facts is not None else prev


def _overrides(dialect: BasePerfDialect, hook: str) -> bool:
    return getattr(type(dialect), hook) is not getattr(BasePerfDialectClass, hook)


def _is_block_loop_body_scope(
    node: Node, dialect: BasePerfDialect, call_kinds: frozenset[str]
) -> bool:
    """True when *node* is the lambda a block-iteration combinator passes as its
    per-iteration body (``ids.forEach { … }``).

    Such a lambda is NOT a deferred scope — it runs once per element, right
    here — so the walker must not reset ``loop_depth`` at its boundary the way
    it does for a genuinely nested function. Ruby needs no such exemption (its
    ``do_block`` is deliberately not a lambda kind), but Kotlin's
    ``lambda_literal`` IS one, and the grammar interposes an ``annotated_lambda``
    between the call and it — hence the two-hop parent walk.
    """
    cur = node
    for _ in range(2):
        parent = cur.parent
        if parent is None:
            return False
        if parent.type in call_kinds:
            body = dialect.block_loop_body(parent)
            # NB: tree-sitter Node wrappers are not singletons — compare with
            # ``==`` (identity by tree + byte range), never ``is``.
            return body is not None and body == cur
        cur = parent
    return False


def _in_deferred_body(node: Node, dialect: BasePerfDialect, scope_kinds: frozenset[str]) -> bool:
    """Whether *node* sits in a body that runs when its result is iterated."""
    cur = node.parent
    while cur is not None and cur.type not in scope_kinds:
        cur = cur.parent
    return cur is not None and dialect.defers_body(cur)


def perf_pass_runs(language: str, lmap: LanguageNodeMap) -> bool:
    """Whether ``_collect_perf_hits`` does anything for *language*."""
    return bool(lmap.call_kinds) and language in PERF_DIALECTS


def _collect_perf_hits(
    root: Node, language: str, lmap: LanguageNodeMap, io_names: dict[str, str]
) -> tuple[list[PerfHit], dict[str, str], list[PerfFnFacts]]:
    """Whole-tree perf pass → ``(hits, io_boundary_names, fn_facts)``.

    An iterative DFS visits each node in a :class:`_Frame` (loop depth, async,
    enclosing function, lock depth). Per-function facts key to the function's
    start line, and a block-scoped lock depth flags an I/O sink under a held
    lock. Loop-body scoping and constant-loop skipping keep only per-iteration
    calls. Returns nothing for languages that opt out (empty ``call_kinds``).

    *io_names* is ``perf.io_boundaries.collect_io_names`` for *root*, which the
    walker's shared file descent collects whenever ``perf_pass_runs``.

    Alongside the same-function ``hits``, it accumulates ``fn_facts`` (one
    :class:`PerfFnFacts` per enclosing function that has any loop-nested call
    or a bare sink), the input to cross-function reachability.
    """
    if not perf_pass_runs(language, lmap):
        return [], {}, []
    return _PerfWalk(root, language, lmap, io_names).run()


class _Frame(NamedTuple):
    """The DFS context a node is visited in."""

    loop_depth: int
    in_async: bool
    func: str | None
    start: int
    lock_depth: int
    # Whether the outermost enclosing loop iterates a collection (not a
    # ``while`` / cursor): the precision gate for ``nested_loop_with_io``.
    outer_iter: bool
    # The innermost data-dependent loop's header line, 0 outside one.
    loop_line: int


_NO_LOOP: tuple[bool, None] = (False, None)


class _Call(NamedTuple):
    node: Node
    method: str
    root_name: str
    awaited: bool
    line: int


class _PerfWalk:
    """One file's perf pass: the dialect's switches, a per-loop memo and the
    per-function accumulators the DFS in :meth:`run` fills."""

    def __init__(
        self, root: Node, language: str, lmap: LanguageNodeMap, io_names: dict[str, str]
    ) -> None:
        self.root = root
        self.lmap = lmap
        self.io_names = io_names
        self.call_kinds = lmap.call_kinds
        self.dialect = dialect = PERF_DIALECTS[language]
        self.has_db_import = any(k == "db" for k in io_names.values())
        markers = dialect.markers
        self.do_string_concat = "string_concat_in_loop" in markers
        self.do_blocking = "blocking_sync_in_async" in markers
        self.do_loop_call_marker = bool(markers & _LOOP_CALL_MARKER_KINDS)
        self.do_loop_stmt_marker = bool(markers & _LOOP_STMT_MARKER_KINDS)
        self.do_bare_call_marker = bool(markers & _BARE_CALL_MARKER_KINDS)
        self.do_loop_iterable_call_marker = bool(markers & _LOOP_ITERABLE_CALL_MARKER_KINDS)
        self.do_serial_await = "serial_await_in_loop" in markers
        self.do_nested_io = "nested_loop_with_io" in markers
        self.do_nested_quadratic = "nested_loop_quadratic" in markers
        self.do_hot_path = "hot_path_sync_io" in markers
        self.do_lock_io = "blocking_io_under_lock" in markers
        # The membership marker's precision gate, computed once per file and only
        # when this dialect can emit that marker.
        self.list_names = (
            dialect.list_bound_names(root)
            if "membership_test_against_list_in_loop" in markers
            else frozenset()
        )
        self.loop_kinds = lmap.loop_kinds
        self.fn_kinds = lmap.function_kinds
        self.lambda_kinds = lmap.lambda_kinds
        self.scope_kinds = self.fn_kinds | self.lambda_kinds
        self.async_fn_kinds = lmap.async_function_kinds
        self.bare_call_wrapper_kinds = lmap.bare_call_wrapper_kinds
        # Hooks a dialect does not override are never called.
        self.do_block_loop = _overrides(dialect, "block_loop_body")
        self.do_bare_stmt_call = bool(self.bare_call_wrapper_kinds) and _overrides(
            dialect, "bare_statement_call"
        )
        self.do_chunked = _overrides(dialect, "is_chunked_loop")
        self.do_magnitude = _overrides(dialect, "loop_source")
        self.do_batch = _overrides(dialect, "batch_form")
        self.do_bound = _overrides(dialect, "concurrency_bound")
        self.do_loop_facts = self.do_chunked or self.do_magnitude or self.do_batch or self.do_bound
        self.do_in_place = _overrides(dialect, "runs_in_place")
        # Node kinds a loop, scope or call handler can apply to: one set test per node.
        self.loop_probe = self.loop_kinds | (self.call_kinds if self.do_block_loop else frozenset())
        self.scope_probe = self.scope_kinds | self.async_fn_kinds
        self.call_probe = self.call_kinds | (
            self.bare_call_wrapper_kinds if self.do_bare_stmt_call else frozenset()
        )
        # An awaited call on a line whose lint suppression says awaiting in this loop
        # is intended (a retry or a stream read) is not a per-iteration finding.
        self.awaits_allowed = (
            dialect.awaits_in_loop_allowed(root.text or b"")
            if _overrides(dialect, "awaits_in_loop_allowed")
            else frozenset()
        )
        # One answer per loop, however many hits sit in it.
        self.sources: dict[tuple[int, int], LoopSource] = {}
        self.hits: list[PerfHit] = []
        # One query written as a chain is several sink calls. The innermost is the
        # one that leaves the process and is the hit; each outer link, met first,
        # leaves here the chain's outermost call and whether any link is awaited.
        self.chain_of: dict[int, tuple[Node, bool]] = {}
        self.access_kinds = lmap.member_access_kinds
        # Per-function ``(name, loop_targets, lock_targets, [bare_sink], misc)`` by start
        # line (0 = module scope). Targets and sink feed cross-function reachability;
        # ``misc`` = [nested_loop_line, blocking_sink_kind, blocking_sink_line] stays out
        # of ``hits``: the engine turns it into hits only for hot functions.
        self.fn_acc: dict[
            int, tuple[str | None, dict[str, int], dict[str, int], list[str | None], list]
        ] = {}
        # func_start -> {call_line: facts} for the loop-call targets above.
        self.call_facts: dict[int, dict[int, LoopFacts]] = {}
        # func_start -> call lines where some helper call is fed by the loop key.
        # A keyed call can have all-default facts (``loop_facts`` returns None) and
        # still has to clear ``key_unused`` written by a key-free call on the line.
        self.keyed_lines: dict[int, set[int]] = {}
        # func_start -> {call_line: loop header line} for the same targets.
        self.call_loop_lines: dict[int, dict[int, int]] = {}
        # ``(first_line, last_line, name)`` of each lambda with no named function
        # around it, so a hit inside one is named for it rather than left blank.
        self.lambda_spans: list[tuple[int, int, str]] = []

    def probe(self, call: Node) -> str | None:
        return self.dialect.call_sink_kind(
            call,
            awaited=self.dialect.is_awaited(call),
            io_names=self.io_names,
            has_db_import=self.has_db_import,
        )

    def source(self, loop: Node) -> LoopSource:
        key = (loop.start_byte, loop.end_byte)
        if key not in self.sources:
            self.sources[key] = self.dialect.loop_source(loop, self.probe) or LoopSource()
        return self.sources[key]

    def sink_facts(
        self, node: Node, loop: Node, sink: bool
    ) -> tuple[BatchForm | None, str | None]:
        """``(batch, concurrency_bound)`` of *node* in *loop*; both ``None`` off a sink."""
        dialect = self.dialect
        batch = dialect.batch_form(node, loop, self.probe) if sink and self.do_batch else None
        bound = dialect.concurrency_bound(node, loop) if sink and self.do_bound else None
        return batch, bound

    def loop_facts(self, node: Node, sink: bool, per_call: bool = False) -> LoopFacts | None:
        """Facts of *node*'s innermost loop; batch and bound only mean something at a sink.

        *per_call* marks the call each iteration makes, a sink or a helper that
        reaches one: only there does it matter whether the loop's key feeds it.
        """
        if not (self.do_loop_facts or per_call):
            return None
        dialect = self.dialect
        loops = _enclosing_loops(node, dialect, self.loop_kinds, self.fn_kinds, self.do_block_loop)
        if not loops:
            return None
        loop = loops[0]
        do_magnitude = self.do_magnitude
        chunked = self.do_chunked and dialect.is_chunked_loop(loop)
        sources = [self.source(lp) for lp in loops] if do_magnitude else []
        magnitude = combined_magnitude(sources) if do_magnitude else "unknown"
        read_tables, small_magnitude = small_table_candidate(sources)
        batch, bound = self.sink_facts(node, loop, sink)
        facts = LoopFacts(
            chunked=chunked,
            magnitude=magnitude,
            read_tables=read_tables,
            small_magnitude=small_magnitude,
            batch=batch,
            concurrency_bound=bound,
            # Only a native loop header names its key; a block loop (Ruby
            # ``each do |row|``) claims nothing.
            key_unused=per_call
            and loop.type in self.loop_kinds
            and not _loop_key_feeds(node, loop),
            iterates=self.source(loop).iterates if do_magnitude else "unknown",
            not_batchable=dialect.not_batchable(node, loop, self.loop_kinds) if per_call else None,
        )
        return facts if facts != LoopFacts() else None

    def _acc(
        self, func_start: int, func_name: str | None
    ) -> tuple[dict[str, int], dict[str, int], list[str | None], list]:
        entry = self.fn_acc.get(func_start)
        if entry is None:
            entry = (func_name, {}, {}, [None], [0, None, 0])
            self.fn_acc[func_start] = entry
        return entry[1], entry[2], entry[3], entry[4]

    def run(self) -> tuple[list[PerfHit], dict[str, str], list[PerfFnFacts]]:
        """The DFS over the file, then the per-line dedupe and per-function facts."""
        loop_probe, scope_probe = self.loop_probe, self.scope_probe
        do_lock_io = self.do_lock_io
        stack: list[tuple[Node, _Frame]] = [(self.root, _Frame(0, False, None, 0, 0, True, 0))]
        while stack:
            node, cur = stack.pop()
            t = node.type
            is_loop, block_loop_body = self._loop_kind(node, t) if t in loop_probe else _NO_LOOP
            nxt = self._scope(node, t, cur) if t in scope_probe else cur
            if is_loop:
                self._on_loop(node, cur, nxt)
            self._visit(node, t, cur, nxt)
            if is_loop:
                self._push_loop_children(stack, node, cur, nxt, block_loop_body)
            elif do_lock_io and self._is_lock_entry(node):
                _push_lock_children(stack, node, nxt)
            else:
                stack.extend([(c, nxt) for c in node.children])
        return _dedupe(self.hits, self.lambda_spans), self.io_names, self._fn_facts()

    def _visit(self, node: Node, t: str, cur: _Frame, nxt: _Frame) -> None:
        """Hits and facts of *node* itself, stamped with the loop the frame names.

        The iterable marker ``_on_loop`` adds sits on the loop's own header and is not stamped.
        """
        hits = self.hits
        stamp_from = len(hits)
        if t in self.call_probe and (call_node := self._call_node(node, t)) is not None:
            self._on_call(call_node, cur, nxt)
        elif cur.loop_depth or (self.do_blocking and cur.in_async):
            self._on_statement(node, cur, nxt)
        if cur.loop_line and len(hits) > stamp_from:
            stamped = hits[stamp_from:]
            hits[stamp_from:] = [replace(h, loop_line=cur.loop_line) for h in stamped]

    def _loop_kind(self, node: Node, t: str) -> tuple[bool, Node | None]:
        """``(is_loop, block_loop_body)``: a data-dependent loop, and the
        per-iteration body when it is a block-iteration call (Ruby ``items.each do``)."""
        # ``is_named`` guards grammars (Ruby) whose keyword tokens share the
        # node-type name of their parent loop.
        is_loop = t in self.loop_kinds and node.is_named
        block_loop_body: Node | None = None
        if not is_loop and self.do_block_loop and t in self.call_kinds:
            block_loop_body = self.dialect.block_loop_body(node)
            if block_loop_body is not None:
                is_loop = True
        if is_loop and self.dialect.is_constant_loop(node):
            return False, None
        return is_loop, block_loop_body

    def _scope(self, node: Node, t: str, cur: _Frame) -> _Frame:
        """The frame *node*'s own visit and children see when it opens a function or lambda.

        A nested function is only defined here, so loop and lock depth reset at
        its boundary. Not reset: a lambda a block-iteration combinator runs per
        element (``ids.forEach { … }``) and a closure the dialect proves runs in
        place. An inline-invoked closure (an IIFE) is reset too: a recall ceiling.
        """
        dialect = self.dialect
        entering_fn = t in self.scope_kinds
        is_async_fn = t in self.async_fn_kinds or (entering_fn and dialect.is_async_fn(node))
        next_async = True if is_async_fn else (False if entering_fn else cur.in_async)
        body_scope = entering_fn and (
            (self.do_block_loop and _is_block_loop_body_scope(node, dialect, self.call_kinds))
            or (self.do_in_place and dialect.runs_in_place(node))
        )
        func, start = self._scope_owner(node, t, cur)
        if body_scope or not entering_fn:
            return cur._replace(in_async=next_async, func=func, start=start)
        return _Frame(0, next_async, func, start, 0, cur.outer_iter, 0)

    def _scope_owner(self, node: Node, t: str, cur: _Frame) -> tuple[str | None, int]:
        """``(func, start)`` the hits under *node* key to; a top-level lambda's span is recorded."""
        if t in self.fn_kinds and is_function_node(node, self.lmap):
            return _perf_func_name(node) or cur.func, node.start_point[0] + 1
        if t in self.lambda_kinds and cur.func is None:
            self.lambda_spans.append(
                (
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                    _find_function_entry_name(node, self.lmap),
                )
            )
        return cur.func, cur.start

    def _on_loop(self, node: Node, cur: _Frame, nxt: _Frame) -> None:
        """Facts and markers a loop node carries on its own header line."""
        dialect = self.dialect
        # A data-dependent loop inside another, iterating the same named
        # collection (all-pairs O(n^2)), is a fact the engine turns into
        # ``nested_loop_quadratic`` only for a hot function.
        if cur.loop_depth >= 1 and self.do_nested_quadratic:
            nm = dialect.loop_iterable_name(node)
            if nm and nm in _enclosing_loop_iterables(
                node, dialect, self.loop_kinds, self.fn_kinds, self.do_block_loop
            ):
                misc = self._acc(nxt.start, nxt.func)[3]
                if misc[0] == 0:
                    misc[0] = node.start_point[0] + 1
        # A loop whose iterable is itself a slow call (``df.iterrows()``) sits in
        # the header, which the body markers never see.
        if self.do_loop_iterable_call_marker:
            icm = dialect.loop_iterable_call_marker(node)
            if icm is not None:
                self.hits.append(
                    PerfHit(icm, node.start_point[0] + 1, nxt.func, "", func_start=nxt.start)
                )

    def _call_node(self, node: Node, t: str) -> Node | None:
        if t in self.call_kinds:
            return node
        if self.do_bare_stmt_call and t in self.bare_call_wrapper_kinds:
            # A parenless call (Pascal ``Q.Open;``) has no call node; the wrapper
            # also holds non-calls (``Exit;``), which the dialect answers None for.
            return self.dialect.bare_statement_call(node)
        return None

    def _on_call(self, call_node: Node, cur: _Frame, nxt: _Frame) -> None:
        dialect = self.dialect
        call = _Call(
            call_node,
            dialect.callee_method_name(call_node) or "",
            dialect.callee_root_name(call_node) or "",
            dialect.is_awaited(call_node),
            call_node.start_point[0] + 1,
        )
        if self.do_bare_call_marker:
            # A call that is its own iteration construct (``.reduce`` with an
            # accumulator spread): a smell at any loop depth.
            bare = dialect.bare_call_marker(call.root_name, call.method, call_node)
            if bare is not None:
                self.hits.append(PerfHit(bare, call.line, nxt.func, "", func_start=nxt.start))
        kind = dialect.call_sink_kind(
            call_node,
            awaited=call.awaited,
            io_names=self.io_names,
            has_db_import=self.has_db_import,
        )
        if kind is None:
            self._on_helper_call(call, cur, nxt)
        else:
            chain = self._sink_chain(call)
            # A link is neither a hit nor a helper: its chain's sink speaks for it.
            if chain is not None:
                self._on_sink(call, kind, chain, cur, nxt)
        if self.do_blocking and cur.in_async and not call.awaited:
            api = dialect.blocking_sync_api(call.root_name, call.method)
            if api is not None and not dialect.task_already_complete(call_node):
                self.hits.append(
                    PerfHit(
                        "blocking_sync_in_async", call.line, nxt.func, api, func_start=nxt.start
                    )
                )

    def _sink_chain(self, call: _Call) -> tuple[Node, bool] | None:
        """``(chain_top, chain_awaited)`` for the sink a chain ends at; ``None`` for a link.

        The innermost sink of a chain is the hit. Each outer link is met first
        and leaves the chain's outermost call and whether any link is awaited.
        """
        call_node = call.node
        inner = None
        for link in _receiver_chain_calls(call_node, self.call_kinds, self.access_kinds):
            if self.probe(link) is not None:
                inner = link
        if inner is not None:
            top, link_awaited = self.chain_of.get(inner.id, (call_node, False))
            self.chain_of[inner.id] = (top, link_awaited or call.awaited)
            return None
        chain_top, link_awaited = self.chain_of.pop(call_node.id, (call_node, False))
        return chain_top, call.awaited or link_awaited

    def _on_sink(
        self, call: _Call, kind: str, chain: tuple[Node, bool], cur: _Frame, nxt: _Frame
    ) -> None:
        """*chain* is ``(chain_top, chain_awaited)`` from :meth:`_sink_chain`."""
        if cur.loop_depth >= 1:
            if not (chain[1] and call.line in self.awaits_allowed):
                self._loop_sink_hits(call, kind, chain, cur, nxt)
        else:
            self._bare_sink_facts(call, kind, nxt)
        if self.do_lock_io and cur.lock_depth >= 1:
            # The round-trip runs under a held lock, serializing every thread.
            self.hits.append(
                PerfHit("blocking_io_under_lock", call.line, nxt.func, kind, func_start=nxt.start)
            )

    def _loop_sink_hits(
        self, call: _Call, kind: str, chain: tuple[Node, bool], cur: _Frame, nxt: _Frame
    ) -> None:
        """``io_in_loop`` and its co-signals for a sink in a loop body."""
        dialect = self.dialect
        chain_top, chain_awaited = chain
        # The loop's key may feed any link, so the chain's facts are read off
        # its outermost call, which holds them all.
        facts = self.loop_facts(chain_top, sink=True, per_call=True)
        sink_call = _sink_call_text(chain_top, dialect.callee_method_name(chain_top) or "", dialect)
        if chain_top is not call.node and not _quotable_chain(sink_call):
            sink_call = _sink_call_text(call.node, call.method, dialect)
        kinds = ["io_in_loop"]
        if self.do_serial_await and chain_awaited:
            # A serial round-trip a ``gather`` / ``Promise.all`` could fan out.
            kinds.append("serial_await_in_loop")
        if self.do_nested_io and cur.loop_depth >= 2 and cur.outer_iter:
            # O(n·m) round-trips, only when the outer loop iterates a collection:
            # a ``while`` pagination cursor around a chunk loop is not one.
            kinds.append("nested_loop_with_io")
        self.hits.extend(
            PerfHit(
                k, call.line, nxt.func, kind,
                func_start=nxt.start, loop=facts, sink_call=sink_call,
            )
            for k in kinds
        )

    def _bare_sink_facts(self, call: _Call, kind: str, nxt: _Frame) -> None:
        """A sink outside any loop makes its function a cross-function reachability target."""
        _lt, _kt, sink_slot, misc = self._acc(nxt.start, nxt.func)
        if sink_slot[0] is None:
            sink_slot[0] = kind
        dialect = self.dialect
        if (
            self.do_hot_path
            and not call.awaited
            and misc[1] is None
            and kind in _HOT_PATH_SINK_KINDS
            and call.method not in dialect.hot_path_excluded_methods
            and not _in_deferred_body(call.node, dialect, self.scope_kinds)
        ):
            # Inherently blocking and noisy everywhere, so a fact: the engine emits
            # ``hot_path_sync_io`` only for a hot, central function. ``db`` and
            # point-sized calls are out (``_HOT_PATH_SINK_KINDS``, ``hot_path_excluded_methods``).
            misc[1] = kind
            misc[2] = call.line

    def _on_helper_call(self, call: _Call, cur: _Frame, nxt: _Frame) -> None:
        """A non-sink call: a loop marker, or a reachability entry in a loop or under a lock."""
        if cur.loop_depth >= 1:
            marker = (
                self.dialect.loop_call_marker(
                    call.root_name, call.method, call.node, self.list_names
                )
                if self.do_loop_call_marker
                else None
            )
            if marker == "lock_in_loop" and _is_lock_acquire_spin(
                call.node, nxt.func, self.dialect, self.loop_kinds, self.fn_kinds
            ):
                marker = None
            if marker is not None:
                self.hits.append(
                    PerfHit(
                        marker, call.line, nxt.func, "", func_start=nxt.start,
                        loop=self.loop_facts(call.node, sink=False),
                        # ``arr.includes(x)``: the list is the receiver.
                        subject=call.root_name if marker == _MEMBERSHIP else "",
                    )
                )
            elif call.method and not (call.awaited and call.line in self.awaits_allowed):
                self._record_loop_call(call, cur, nxt)
        if self.do_lock_io and cur.lock_depth >= 1 and call.method:
            lock_targets = self._acc(nxt.start, nxt.func)[1]
            if call.method not in lock_targets:
                lock_targets[call.method] = call.line

    def _record_loop_call(self, call: _Call, cur: _Frame, nxt: _Frame) -> None:
        """A loop-nested helper call, kept at the first line it is seen."""
        targets = self._acc(nxt.start, nxt.func)[0]
        if call.method in targets:
            return
        line, start = call.line, nxt.start
        targets[call.method] = line
        if cur.loop_line:
            self.call_loop_lines.setdefault(start, {})[line] = cur.loop_line
        facts = self.loop_facts(call.node, sink=False, per_call=True)
        # Same innermost loop ``loop_facts`` just read. A keyed call clears
        # ``key_unused`` for the whole line, even when its own facts are default.
        loops = _enclosing_loops(
            call.node, self.dialect, self.loop_kinds, self.fn_kinds, self.do_block_loop
        )
        this_keyed = bool(loops) and _loop_key_feeds(call.node, loops[0])
        if this_keyed:
            self.keyed_lines.setdefault(start, set()).add(line)
        merged = _call_line_facts(
            self.call_facts.get(start, {}).get(line),
            facts,
            this_keyed=this_keyed,
            line_keyed=line in self.keyed_lines.get(start, ()),
        )
        bucket = self.call_facts.get(start)
        if merged is None:
            if bucket is not None:
                bucket.pop(line, None)
        elif bucket is None:
            self.call_facts[start] = {line: merged}
        else:
            bucket[line] = merged

    def _on_statement(self, node: Node, cur: _Frame, nxt: _Frame) -> None:
        """Markers on a non-call node: a blocking member read, string concat, statement markers."""
        dialect = self.dialect
        if self.do_blocking and cur.in_async:
            # A non-call member read that blocks in async (C# ``task.Result``).
            mem = dialect.async_blocking_member(node)
            if mem is not None and not dialect.task_already_complete(node):
                self.hits.append(
                    PerfHit(
                        "blocking_sync_in_async",
                        node.start_point[0] + 1,
                        nxt.func,
                        mem,
                        func_start=nxt.start,
                    )
                )
        if cur.loop_depth < 1:
            return
        if self.do_string_concat and dialect.is_string_concat(node):
            self.hits.append(
                PerfHit(
                    "string_concat_in_loop",
                    node.start_point[0] + 1,
                    nxt.func,
                    "",
                    func_start=nxt.start,
                    loop=self.loop_facts(node, sink=False),
                    subject=_concat_target(node),
                )
            )
        elif self.do_loop_stmt_marker:
            self._on_loop_statement(node, nxt)

    def _on_loop_statement(self, node: Node, nxt: _Frame) -> None:
        sm = self.dialect.loop_stmt_marker(node, self.list_names)
        if sm == "lock_in_loop" and _is_lock_acquire_spin(
            node, nxt.func, self.dialect, self.loop_kinds, self.fn_kinds
        ):
            sm = None
        if sm is None:
            return
        self.hits.append(
            PerfHit(
                sm,
                node.start_point[0] + 1,
                nxt.func,
                "",
                func_start=nxt.start,
                loop=self.loop_facts(node, sink=False),
                # ``x in big``: the list is the last operand.
                subject=(
                    _subject_text(next(iter(reversed(node.named_children)), None))
                    if sm == _MEMBERSHIP
                    else ""
                ),
            )
        )

    def _is_lock_entry(self, node: Node) -> bool:
        """A block-scoped lock (``lock`` / ``synchronized``) whose body runs with it held.

        Only the body: the lock-object expression runs before the lock is taken.
        """
        return self.dialect.is_lock_scope(node) and not _is_memoizing_lock(node)

    def _push_loop_children(
        self,
        stack: list[tuple[Node, _Frame]],
        node: Node,
        cur: _Frame,
        nxt: _Frame,
        block_loop_body: Node | None,
    ) -> None:
        """Only the loop BODY runs per iteration; the header runs once."""
        # The outermost loop of a nest fixes ``outer_iter``; deeper loops inherit it.
        outer_iter = cur.outer_iter if cur.loop_depth >= 1 else self.dialect.is_iteration_loop(node)
        body = block_loop_body
        if body is None:
            body = self.dialect.loop_body(node)
        inside = nxt._replace(
            loop_depth=nxt.loop_depth + 1, outer_iter=outer_iter, loop_line=node.start_point[0] + 1
        )
        if body is None:
            stack.extend((c, inside) for c in node.children)
            return
        header = nxt._replace(outer_iter=outer_iter)
        # Node wrappers are not singletons: compare with ``==``, never ``is``.
        stack.extend((c, inside if c == body else header) for c in node.children)

    def _fn_facts(self) -> list[PerfFnFacts]:
        """One :class:`PerfFnFacts` per function with a call target, a sink or a fact."""
        fn_facts = [
            PerfFnFacts(
                function=name,
                func_start=start,
                loop_call_targets=tuple(sorted(loop_targets.items())),
                bare_sink_kind=sink[0],
                lock_call_targets=tuple(sorted(lock_targets.items())),
                nested_loop_line=misc[0],
                blocking_sink_kind=misc[1],
                blocking_sink_line=misc[2],
                loop_call_facts=tuple(sorted(self.call_facts.get(start, {}).items())),
                loop_call_lines=tuple(sorted(self.call_loop_lines.get(start, {}).items())),
            )
            for start, (name, loop_targets, lock_targets, sink, misc) in self.fn_acc.items()
            if (loop_targets or lock_targets or sink[0] is not None or misc[0] or misc[1] is not None)
        ]
        fn_facts.sort(key=lambda f: f.func_start)
        return fn_facts


def _push_lock_children(stack: list[tuple[Node, _Frame]], node: Node, nxt: _Frame) -> None:
    """Raise the lock depth for the lock's body block only."""
    held = nxt._replace(lock_depth=nxt.lock_depth + 1)
    stack.extend([(c, held if c.type in _LOCK_BODY_KINDS else nxt) for c in node.children])


def _dedupe(hits: list[PerfHit], lambda_spans: list[tuple[int, int, str]]) -> list[PerfHit]:
    """One finding per (kind, line, function), keeping a call the loop's key reaches.

    A chain of sinks is one hit already; what is left are two hits of one kind
    sharing a line without being one chain, such as two membership tests in one
    condition.
    """
    kept: dict[tuple[str, int, str | None], int] = {}
    deduped: list[PerfHit] = []
    for h in _name_lambda_hits(hits, lambda_spans):
        key = (h.kind, h.line, h.function)
        at = kept.get(key)
        if at is None:
            kept[key] = len(deduped)
            deduped.append(h)
        elif _key_unused(deduped[at]) and not _key_unused(h):
            deduped[at] = h
    deduped.sort(key=lambda h: (h.line, h.kind))
    return deduped
