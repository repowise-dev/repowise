"""Centrality-gated Phase-7b markers (the differentiator).

Two passes that run after the walker, over the resolved ``calls`` graph, using
the :class:`~.ranking.PerfRanker` (Primitive 2) and the sink-agnostic
reachability engine (Primitive 3):

  * :func:`collect_centrality_gated` — turns the walker's per-function *facts*
    (``PerfFnFacts.nested_loop_line`` / ``blocking_sink_{kind,line}``) into
    ``nested_loop_quadratic`` and ``hot_path_sync_io`` hits, but ONLY for a
    function the ranker calls *hot* (top-quintile call-graph centrality or a
    churny/hotspot file). The shapes are unambiguous but noisy when flagged
    everywhere; the gate IS the precision fix the backlog deferred the O(n^2)
    marker for. Keeping the generation here (not in the walker) leaves the raw
    walker output the same high-precision same-function set it has always been.

  * :func:`collect_blocking_io_under_lock` — the cross-function lock→I/O case: a
    function holds a lock around a call to a helper that, within a few hops,
    executes an I/O boundary. Pure moat: it reuses the identical reverse-BFS the
    cross-function N+1 pass uses, only the *entry set* differs (callees invoked
    while a lock is held, ``PerfFnFacts.lock_call_targets``, instead of every
    loop-nested callee).

Both are failure-isolated by their callers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .callgraph import CallGraphIndex
from .reachability import path_to_sink, reachable_to_sink

if TYPE_CHECKING:
    from ..complexity import FileComplexity, PerfFnFacts, PerfHit
    from .ranking import PerfRanker

# The boundary-kind detail convention shared with the cross-function N+1 pass.
LOCK_IO_KIND = "blocking_io_under_lock"


def collect_centrality_gated(
    walked: Iterable[tuple[Any, FileComplexity]], ranker: PerfRanker
) -> dict[str, list[PerfHit]]:
    """Centrality-gated ``nested_loop_quadratic`` / ``hot_path_sync_io`` hits.

    Keyed by file path. For every function carrying the corresponding fact, a hit
    is produced ONLY when ``ranker.is_hot(path, func_start)`` — so a quadratic
    loop or a blocking sync sink ships only where it sits on a hot, central, or
    churny path. Pure when neither graph nor git signal is available (nothing is
    hot ⇒ no hits), which is the precision-first default.
    """
    from ..complexity import PerfHit

    out: dict[str, list[PerfHit]] = {}
    for pf, fcx in walked:
        if not fcx.perf_fn_facts:
            continue
        path = pf.file_info.path
        file_hits: list[PerfHit] = []
        for fact in fcx.perf_fn_facts:
            if fact.nested_loop_line == 0 and fact.blocking_sink_kind is None:
                continue
            if not ranker.is_hot(path, fact.func_start):
                continue
            if fact.nested_loop_line:
                file_hits.append(
                    PerfHit(
                        kind="nested_loop_quadratic",
                        line=fact.nested_loop_line,
                        function=fact.function,
                        detail="",
                        func_start=fact.func_start,
                    )
                )
            if fact.blocking_sink_kind is not None:
                file_hits.append(
                    PerfHit(
                        kind="hot_path_sync_io",
                        line=fact.blocking_sink_line,
                        function=fact.function,
                        detail=fact.blocking_sink_kind,
                        func_start=fact.func_start,
                    )
                )
        if file_hits:
            out[path] = file_hits
    return out


def collect_blocking_io_under_lock(
    walked: Iterable[tuple[Any, FileComplexity]],
    graph: Any,
    *,
    index: CallGraphIndex | None = None,
    max_depth: int = 3,
) -> dict[str, list[PerfHit]]:
    """Cross-function ``blocking_io_under_lock`` hits, keyed by the lock-owning file.

    A function acquires a lock and, while holding it, calls a helper that reaches
    an I/O boundary within ``max_depth`` hops — the I/O round-trip runs under the
    lock, serializing every thread on a network/db/fs wait. Mirrors
    ``crossfn.collect_crossfn_io_in_loop`` exactly: the only difference is the
    entry set is ``PerfFnFacts.lock_call_targets`` (callees invoked under a held
    lock) instead of loop-nested callees. The same-function case (an I/O sink
    lexically inside a ``lock``/``synchronized`` block) is emitted directly by
    the walker.
    """
    from ..complexity import PerfHit

    walked_list = list(walked)
    if graph is None or not walked_list:
        return {}

    has_sink = any(
        fact.bare_sink_kind is not None for _pf, fcx in walked_list for fact in fcx.perf_fn_facts
    )
    has_entry = any(
        fact.lock_call_targets for _pf, fcx in walked_list for fact in fcx.perf_fn_facts
    )
    if not (has_sink and has_entry):
        return {}

    if index is None:
        index = CallGraphIndex(graph)
    if not index.forward:
        return {}

    sink_kind: dict[str, str] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        for fact in fcx.perf_fn_facts:
            if fact.bare_sink_kind is None:
                continue
            sid = index.resolve_function(path, fact.func_start)
            if sid is not None:
                sink_kind.setdefault(sid, fact.bare_sink_kind)
    if not sink_kind:
        return {}

    reach = reachable_to_sink(
        sink_kind.keys(),
        lambda node: index.reverse.get(node, ()),
        max_depth=max_depth,
    )

    out: dict[str, list[PerfHit]] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        for fact in fcx.perf_fn_facts:
            if not fact.lock_call_targets:
                continue
            hits = _lock_hits_for_function(path, fact, index, reach, sink_kind, PerfHit)
            if hits:
                out.setdefault(path, []).extend(hits)
    return out


def _lock_hits_for_function(
    path: str,
    fact: PerfFnFacts,
    index: CallGraphIndex,
    reach: dict[str, Any],
    sink_kind: dict[str, str],
    perf_hit_cls: type,
) -> list[PerfHit]:
    a_sid = index.resolve_function(path, fact.func_start)
    if a_sid is None:
        return []
    callees = index.forward.get(a_sid)
    if not callees:
        return []
    callees_by_name: dict[str, list[str]] = {}
    for c in callees:
        callees_by_name.setdefault(index.name.get(c, ""), []).append(c)

    hits: list[PerfHit] = []
    seen: set[str] = set()
    for target_name, call_line in fact.lock_call_targets:
        if target_name in seen:
            continue
        for callee in callees_by_name.get(target_name, ()):
            info = reach.get(callee)
            if info is None:
                continue
            chain = path_to_sink(callee, reach)
            if not chain:
                continue
            seen.add(target_name)
            kind = sink_kind.get(info.sink, "")
            hits.append(
                perf_hit_cls(
                    kind=LOCK_IO_KIND,
                    line=call_line,
                    function=fact.function,
                    detail=kind,
                    func_start=fact.func_start,
                    path=(a_sid, *chain),
                )
            )
            break
    return hits
