"""Performance-risk detection (the ``performance`` health dimension).

io_in_loop / string_concat_in_loop / blocking_sync_in_async and the Phase-7
loop-level / centrality-gated markers.

One whole-tree pass mirroring ``_collect_error_handling`` but carrying the
per-node context the perf signal needs: loop depth, in-async, lock depth, and
the enclosing function name. Two non-negotiable refinements (Phase-0 gate: they
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

from typing import TYPE_CHECKING

from ..perf.dialects import PERF_DIALECTS
from ..perf.io_boundaries import collect_io_names
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
# / Java ``synchronized (x) {…}``). Only the body runs with the lock held, so
# ``lock_depth`` is raised for the body child only — a sink in the lock-object
# expression (``synchronized(repo.find(id)){…}``) runs BEFORE the lock is taken.
_LOCK_BODY_KINDS = frozenset({"block", "statement_block", "compound_statement"})


def _perf_func_name(node: Node) -> str | None:
    nm = node.child_by_field_name("name")
    if nm is not None and nm.text:
        return nm.text.decode("utf-8", "replace")
    return None


def _enclosing_loop_iterables(
    node: Node, dialect: BasePerfDialect, loop_kinds: frozenset[str], fn_kinds: frozenset[str]
) -> set[str]:
    """Names of the collections every enclosing loop (up to the function bound)
    iterates over — the lookup the same-collection ``nested_loop_quadratic``
    shape gate compares the inner loop's iterable against."""
    names: set[str] = set()
    cur = node.parent
    for _ in range(64):
        if cur is None or cur.type in fn_kinds:
            break
        if cur.type in loop_kinds:
            nm = dialect.loop_iterable_name(cur)
            if nm:
                names.add(nm)
        cur = cur.parent
    return names


def _collect_perf_hits(
    root: Node, language: str, lmap: LanguageNodeMap
) -> tuple[list[PerfHit], dict[str, str], list[PerfFnFacts]]:
    """Whole-tree perf pass → ``(hits, io_boundary_names, fn_facts)``.

    Iterative DFS carrying ``(node, loop_depth, in_async, func_name,
    func_start, lock_depth)`` — the proven Phase-0 shape, extended with the
    enclosing function's start line (so per-function facts key to a graph symbol
    node) and a block-scoped ``lock_depth`` (so an I/O sink under a held lock is
    flagged). Loop-body scoping and constant-loop skipping are applied so only
    genuinely per-iteration calls are flagged. Returns nothing for languages
    that opt out of the perf pass (empty ``call_kinds``).

    Alongside the same-function ``hits``, it accumulates ``fn_facts`` (one
    :class:`PerfFnFacts` per enclosing function that has any loop-nested call
    or a bare sink) — the input to PR4's cross-function reachability.
    """
    call_kinds = lmap.call_kinds
    dialect = PERF_DIALECTS.get(language)
    if not call_kinds or dialect is None:
        return [], {}, []

    io_names = collect_io_names(root, language)
    has_db_import = any(k == "db" for k in io_names.values())
    markers = dialect.markers
    do_string_concat = "string_concat_in_loop" in markers
    do_blocking = "blocking_sync_in_async" in markers
    do_loop_call_marker = bool(markers & _LOOP_CALL_MARKER_KINDS)
    do_loop_stmt_marker = bool(markers & _LOOP_STMT_MARKER_KINDS)
    do_bare_call_marker = bool(markers & _BARE_CALL_MARKER_KINDS)
    do_loop_iterable_call_marker = bool(markers & _LOOP_ITERABLE_CALL_MARKER_KINDS)
    do_serial_await = "serial_await_in_loop" in markers
    # Phase 7b markers.
    do_nested_io = "nested_loop_with_io" in markers
    do_nested_quadratic = "nested_loop_quadratic" in markers
    do_hot_path = "hot_path_sync_io" in markers
    do_lock_io = "blocking_io_under_lock" in markers
    # ``list_names`` is the precision gate for the membership marker; compute it
    # once per file only when this dialect can emit that marker, then thread it
    # to the loop-marker hooks (which ignore it for every other marker).
    list_names = (
        dialect.list_bound_names(root)
        if "membership_test_against_list_in_loop" in markers
        else frozenset()
    )
    loop_kinds = lmap.loop_kinds
    fn_kinds = lmap.function_kinds
    lambda_kinds = lmap.lambda_kinds
    async_fn_kinds = lmap.async_function_kinds

    hits: list[PerfHit] = []
    # Per-enclosing-function accumulators keyed by the function's start line
    # (0 = module scope): ``(name, loop_targets{name->line}, lock_targets{...},
    # [bare_sink], misc)``. loop/lock targets feed the cross-function
    # reachability passes; the bare-sink slot makes the function a reachability
    # target. ``misc`` carries the Phase-7b *centrality-gated* facts the engine
    # turns into hits only for hot functions (kept out of ``hits`` so the raw
    # walker output stays the same high-precision same-function set):
    #   misc[0] = nested_loop_line   (a data-dependent loop nested in another)
    #   misc[1] = blocking_sink_kind (first non-awaited loop_depth-0 sink kind)
    #   misc[2] = blocking_sink_line
    fn_acc: dict[
        int, tuple[str | None, dict[str, int], dict[str, int], list[str | None], list]
    ] = {}

    def _acc(
        func_start: int, func_name: str | None
    ) -> tuple[dict[str, int], dict[str, int], list[str | None], list]:
        entry = fn_acc.get(func_start)
        if entry is None:
            entry = (func_name, {}, {}, [None], [0, None, 0])
            fn_acc[func_start] = entry
        return entry[1], entry[2], entry[3], entry[4]

    # (node, loop_depth, in_async, func_name, func_start, lock_depth, outer_iter)
    # ``outer_iter`` is whether the OUTERMOST enclosing loop iterates a collection
    # (vs a ``while``/cursor) — the precision gate for ``nested_loop_with_io``.
    stack: list[tuple[Node, int, bool, str | None, int, int, bool]] = [
        (root, 0, False, None, 0, 0, True)
    ]
    while stack:
        node, loop_depth, in_async, func_name, func_start, lock_depth, outer_iter = stack.pop()
        t = node.type

        is_loop = t in loop_kinds
        if is_loop and dialect.is_constant_loop(node):
            is_loop = False

        entering_fn = t in fn_kinds or t in lambda_kinds
        is_async_fn = t in async_fn_kinds or (entering_fn and dialect.is_async_fn(node))
        next_async = True if is_async_fn else (False if entering_fn else in_async)
        next_func = func_name
        next_start = func_start
        if t in fn_kinds:
            next_func = _perf_func_name(node) or func_name
            next_start = node.start_point[0] + 1

        # A data-dependent loop nested inside another (``loop_depth`` only counts
        # non-constant loops) is an O(n^2) shape. Record the site as a fact; the
        # engine emits ``nested_loop_quadratic`` only when the function is hot
        # (centrality gate), so the noisy-everywhere shape never ships ungated.
        if is_loop and loop_depth >= 1 and do_nested_quadratic:
            # Shape gate (Phase-7d): raw nesting depth was ~3-20% precision in
            # every language. Fire only on the SAME-COLLECTION shape — the inner
            # loop iterates the same named collection as an enclosing loop
            # (all-pairs O(n^2)) — which all four Phase-7c labelers converged on.
            nm = dialect.loop_iterable_name(node)
            if nm and nm in _enclosing_loop_iterables(node, dialect, loop_kinds, fn_kinds):
                misc = _acc(next_start, next_func)[3]
                if misc[0] == 0:
                    misc[0] = node.start_point[0] + 1

        # A loop whose ITERABLE is itself a slow call (``for _, r in
        # df.iterrows()``): the call sits in the loop header (runs once), so the
        # body call markers never see it. Fire on the loop node itself, at any
        # nesting depth — ``iterrows`` is O(n)-boxing slow on its own.
        if is_loop and do_loop_iterable_call_marker:
            icm = dialect.loop_iterable_call_marker(node)
            if icm is not None:
                hits.append(
                    PerfHit(icm, node.start_point[0] + 1, next_func, "", func_start=next_start)
                )

        if t in call_kinds:
            method = dialect.callee_method_name(node) or ""
            root_name = dialect.callee_root_name(node) or ""
            parent = node.parent
            awaited = parent is not None and "await" in parent.type
            line = node.start_point[0] + 1
            if do_bare_call_marker:
                # A call that is its own iteration construct (``.reduce`` with an
                # accumulator spread) — a perf smell at any loop depth.
                bare = dialect.bare_call_marker(root_name, method, node)
                if bare is not None:
                    hits.append(PerfHit(bare, line, next_func, "", func_start=next_start))
            kind = dialect.sink_kind(
                root_name,
                method,
                awaited=awaited,
                is_attribute=dialect.callee_is_attribute(node),
                io_names=io_names,
                has_db_import=has_db_import,
            )
            if kind is not None:
                if loop_depth >= 1:
                    hits.append(PerfHit("io_in_loop", line, next_func, kind, func_start=next_start))
                    if do_serial_await and awaited:
                        # An *awaited* sink in a loop body is additionally a
                        # missed-concurrency candidate (a serial round-trip that
                        # a ``gather`` / ``Promise.all`` could fan out). Advisory
                        # co-signal alongside ``io_in_loop``; ``detail`` carries
                        # the boundary kind for the finding.
                        hits.append(
                            PerfHit(
                                "serial_await_in_loop", line, next_func, kind, func_start=next_start
                            )
                        )
                    if do_nested_io and loop_depth >= 2 and outer_iter:
                        # A sink in the inner body of a NESTED loop -> O(n·m)
                        # round-trips. Nesting raises confidence, so it ships
                        # un-gated alongside ``io_in_loop`` — but only when the
                        # OUTER loop iterates a collection (``outer_iter``): a
                        # ``while`` pagination cursor wrapping an inner ``for ...
                        # of chunk`` is ``io_in_loop``, not a nested explosion
                        # (Phase-7c TS while-cursor FP).
                        hits.append(
                            PerfHit(
                                "nested_loop_with_io", line, next_func, kind, func_start=next_start
                            )
                        )
                else:
                    # A sink at loop_depth 0 makes this function a reachability
                    # target: a loop elsewhere calling into it runs the sink N
                    # times (cross-function N+1).
                    _lt, _kt, sink_slot, misc = _acc(next_start, next_func)
                    if sink_slot[0] is None:
                        sink_slot[0] = kind
                    if (
                        do_hot_path
                        and not awaited
                        and misc[1] is None
                        and kind in _HOT_PATH_SINK_KINDS
                    ):
                        # An inherently-blocking (non-awaited subprocess / fs /
                        # sync-network) sink outside any loop. Noisy everywhere,
                        # so record it as a fact; the engine emits
                        # ``hot_path_sync_io`` only for a hot, request-reachable
                        # function (centrality gate). ``db`` is excluded — see
                        # ``_HOT_PATH_SINK_KINDS``.
                        misc[1] = kind
                        misc[2] = line
                if do_lock_io and lock_depth >= 1:
                    # A sink reached while a block-scoped lock is held: the
                    # round-trip runs under the lock, serializing every thread.
                    hits.append(
                        PerfHit(
                            "blocking_io_under_lock", line, next_func, kind, func_start=next_start
                        )
                    )
            else:
                if loop_depth >= 1:
                    marker = (
                        dialect.loop_call_marker(root_name, method, node, list_names)
                        if do_loop_call_marker
                        else None
                    )
                    if marker is not None:
                        hits.append(PerfHit(marker, line, next_func, "", func_start=next_start))
                    elif method:
                        # A loop-nested call to a non-sink helper: a candidate
                        # entry for cross-function reachability (PR4). Keep the
                        # first line we see the helper called at, for the finding.
                        targets = _acc(next_start, next_func)[0]
                        if method not in targets:
                            targets[method] = line
                if do_lock_io and lock_depth >= 1 and method:
                    # A non-sink call under a held lock: a candidate entry for the
                    # cross-function ``blocking_io_under_lock`` reachability pass.
                    lock_targets = _acc(next_start, next_func)[1]
                    if method not in lock_targets:
                        lock_targets[method] = line
            if do_blocking and in_async and not awaited:
                api = dialect.blocking_sync_api(root_name, method)
                if api is not None:
                    hits.append(
                        PerfHit(
                            "blocking_sync_in_async", line, next_func, api, func_start=next_start
                        )
                    )
        else:
            if do_blocking and in_async:
                # A non-call member read that blocks in async (C# ``task.Result``).
                mem = dialect.async_blocking_member(node)
                if mem is not None:
                    hits.append(
                        PerfHit(
                            "blocking_sync_in_async",
                            node.start_point[0] + 1,
                            next_func,
                            mem,
                            func_start=next_start,
                        )
                    )
            if loop_depth >= 1:
                if do_string_concat and dialect.is_string_concat(node):
                    hits.append(
                        PerfHit(
                            "string_concat_in_loop",
                            node.start_point[0] + 1,
                            next_func,
                            "",
                            func_start=next_start,
                        )
                    )
                elif do_loop_stmt_marker:
                    sm = dialect.loop_stmt_marker(node, list_names)
                    if sm is not None:
                        hits.append(
                            PerfHit(
                                sm,
                                node.start_point[0] + 1,
                                next_func,
                                "",
                                func_start=next_start,
                            )
                        )

        # A block-scoped lock (``lock``/``synchronized``) opens a held region.
        # Only its BLOCK body runs with the lock held — a sink in the lock-object
        # expression (``synchronized(repo.find(id)){…}``) runs before the lock is
        # taken — so ``lock_depth`` is raised per-child, for the body block only.
        entering_lock = do_lock_io and dialect.is_lock_scope(node)

        if is_loop:
            # The outermost loop in a nest fixes ``outer_iter``; deeper loops
            # inherit it. Set when entering the first loop (loop_depth 0 -> 1).
            next_outer_iter = outer_iter if loop_depth >= 1 else dialect.is_iteration_loop(node)
            # Only the loop BODY runs per-iteration; the ``for x in <iterable>``
            # header / ``while <cond>`` condition runs once.
            body = node.child_by_field_name("body")
            if body is not None:
                # NB: tree-sitter Node wrappers are not singletons, so compare
                # with ``==`` (identity by tree + byte range), never ``is``.
                for c in node.children:
                    cd = loop_depth + 1 if c == body else loop_depth
                    stack.append(
                        (c, cd, next_async, next_func, next_start, lock_depth, next_outer_iter)
                    )
            else:
                for c in node.children:
                    stack.append(
                        (
                            c,
                            loop_depth + 1,
                            next_async,
                            next_func,
                            next_start,
                            lock_depth,
                            next_outer_iter,
                        )
                    )
        elif entering_lock:
            # Raise lock_depth for the body block only (not the lock-object expr).
            for c in node.children:
                cl = lock_depth + 1 if c.type in _LOCK_BODY_KINDS else lock_depth
                stack.append((c, loop_depth, next_async, next_func, next_start, cl, outer_iter))
        else:
            for c in node.children:
                stack.append(
                    (c, loop_depth, next_async, next_func, next_start, lock_depth, outer_iter)
                )

    # Dedup chained sinks: ``result.scalars().all()`` parses as two call nodes
    # on one line (the ``.scalars()`` sink and the ``.all()`` materializer) —
    # one logical query, one finding. Collapse per (kind, line, function).
    seen: set[tuple[str, int, str | None]] = set()
    deduped: list[PerfHit] = []
    for h in hits:
        key = (h.kind, h.line, h.function)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    deduped.sort(key=lambda h: (h.line, h.kind))

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
        )
        for start, (name, loop_targets, lock_targets, sink, misc) in fn_acc.items()
        if (loop_targets or lock_targets or sink[0] is not None or misc[0] or misc[1] is not None)
    ]
    fn_facts.sort(key=lambda f: f.func_start)
    return deduped, io_names, fn_facts
