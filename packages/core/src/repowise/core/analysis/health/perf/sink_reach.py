"""Shared entry-set to sink reachability collector.

Two of the engine's differentiating markers ask the same question of the same
graph and differ only in which callees count as an entry:

  * :func:`.crossfn.collect_crossfn_io_in_loop` enters from the callees a
    function invokes inside a loop body (``PerfFnFacts.loop_call_targets``);
  * :func:`.gated.collect_blocking_io_under_lock` enters from the callees it
    invokes while holding a lock (``PerfFnFacts.lock_call_targets``).

Everything between those two ends was written twice: the same pre-checks, the
same index, the same bare-sink set, the same multi-source reverse BFS, and the
same per-function first-hop resolution. A fix to the walk had to be made in
both places or it silently applied to one marker only, which is what this
module exists to prevent.

First-hop resolution goes through :meth:`ExecutionGraphIndex.resolve_call_targets`
and each hit carries the ``resolution_basis`` it came back with, so the
exact-edge matching stays exact here -- collapsing the two copies must not cost
the call-line accuracy the shared index provides.

The soundness limits are the ones documented on :mod:`.crossfn` -- static call
graph, depth bound, and incremental runs -- since this is that walk, not a new
one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .callgraph import CallGraphIndex
from .reachability import path_to_sink, reachable_to_sink

if TYPE_CHECKING:
    # Imported lazily at runtime (see ``_hits_for_function``) to avoid a cycle:
    # the walker pulls in ``perf.io_boundaries`` while the ``complexity``
    # package is still initialising, so this module cannot import it eagerly.
    from ..complexity import FileComplexity, PerfFnFacts, PerfHit

# ``(callee name, call line)`` pairs: which of a function's callees are entries.
EntrySelector = Callable[["PerfFnFacts"], Sequence[tuple[str, int]]]


def collect_sink_reaching_hits(
    walked: Iterable[tuple[Any, FileComplexity]],
    graph: Any,
    *,
    entries: EntrySelector,
    kind: str,
    index: CallGraphIndex | None = None,
    max_depth: int = 3,
    carry_func_start: bool = False,
) -> dict[str, list[PerfHit]]:
    """Hits for entry callees that reach a bare I/O sink, keyed by owning file.

    ``entries`` selects the entry callees from a function's facts and ``kind``
    is the :class:`~..complexity.PerfHit` kind the caller reports under. See the
    two callers for what each combination means.

    ``carry_func_start`` sets ``PerfHit.func_start``. It exists because the two
    callers disagree today: the lock pass carries it and the loop pass does not.
    Preserved rather than unified, since making them agree is a behaviour change
    and this is a de-duplication.
    """
    walked_list = list(walked)
    if graph is None or not walked_list:
        return {}

    # Cheap pre-checks over the already-computed facts, before touching the
    # graph at all: a hit needs BOTH a function holding a bare sink (a
    # reachability target) and a function with an entry callee. On a repo with
    # neither, this returns without scanning the graph.
    has_sink = any(
        fact.bare_sink_kind is not None for _pf, fcx in walked_list for fact in fcx.perf_fn_facts
    )
    has_entry = any(entries(fact) for _pf, fcx in walked_list for fact in fcx.perf_fn_facts)
    if not (has_sink and has_entry):
        return {}

    if index is None:
        index = CallGraphIndex(graph)
    if not index.forward:
        return {}  # no resolved call edges → nothing cross-function to find

    # --- sink set: functions that execute a bare (loop_depth 0) I/O sink ------
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

    # --- match each owner's entry callees against the reach map ---------------
    out: dict[str, list[PerfHit]] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        for fact in fcx.perf_fn_facts:
            if not entries(fact):
                continue
            hits = _hits_for_function(
                path,
                fact,
                index,
                reach,
                sink_kind,
                entries=entries,
                kind=kind,
                carry_func_start=carry_func_start,
            )
            if hits:
                out.setdefault(path, []).extend(hits)
    return out


def _hits_for_function(
    path: str,
    fact: PerfFnFacts,
    index: CallGraphIndex,
    reach: dict[str, Any],
    sink_kind: dict[str, str],
    *,
    entries: EntrySelector,
    kind: str,
    carry_func_start: bool,
) -> list[PerfHit]:
    from ..complexity import PerfHit

    a_sid = index.resolve_function(path, fact.func_start)
    if a_sid is None:
        return []
    callees = index.forward.get(a_sid)
    if not callees:
        return []

    extra: dict[str, Any] = {"func_start": fact.func_start} if carry_func_start else {}
    hits: list[PerfHit] = []
    seen: set[str] = set()
    for target_name, call_line in entries(fact):
        if target_name in seen:
            continue
        # Call-line accurate first-hop resolution: the index picks the exact
        # graph edge for this call site and reports how it got there, so the
        # basis travels onto the hit rather than being re-derived by name.
        targets, basis = index.resolve_call_targets(a_sid, call_line, target_name)
        for callee in targets:
            info = reach.get(callee)
            if info is None:
                continue
            chain = path_to_sink(callee, reach)
            if not chain:
                continue
            seen.add(target_name)
            hits.append(
                PerfHit(
                    kind=kind,
                    line=call_line,
                    function=fact.function,
                    detail=sink_kind.get(info.sink, ""),
                    path=(a_sid, *chain),
                    resolution_basis=basis,
                    **extra,
                )
            )
            break
    return hits
