"""Cross-function io-in-loop / N+1 detection (the Tier-B moat).

The same-function detector (``complexity.walker`` + ``biomarkers.io_in_loop``)
catches a sink *written directly inside* a loop. The pattern no commodity
linter ships is the **cross-function** one: a loop in function ``A`` calls a
helper ``B`` (directly, or transitively within a few hops) and ``B`` executes an
I/O boundary. Each loop iteration pays for ``B``'s round-trip — an N+1 — but the
loop and the sink live in different functions, so a file-local pass cannot see
it.

This module is the bridge between three things that already exist:

  * the walker's per-function facts (:class:`PerfFnFacts`) — which callees each
    function invokes inside a loop body, and which functions hold a *bare*
    (non-loop) sink;
  * the resolved symbol-level ``calls`` graph the engine already has (file +
    symbol nodes, ``calls`` edges between ``path::name`` symbol ids);
  * the sink-agnostic :mod:`.reachability` walk (Primitive 3).

It runs **once per analyze()** and is ``O(V + E)`` bounded by ``max_depth``:
one pass to index symbol nodes, one pass to extract ``calls`` adjacency, one
multi-source reverse BFS from the sink-holding functions, then a cheap
per-function lookup. No new graph is built; the depth bound and the
BFS-from-sinks memoization keep it inside the index-time budget.

Soundness (honest limits — these cap recall, never precision):

  * **Static call graph.** Dynamic dispatch, monkeypatching, callbacks passed
    as values, and reflection produce no ``calls`` edge, so a sink reached only
    that way is invisible. ORM lazy-loading (a sink that fires on attribute
    access, not a visible call) is likewise out of reach.
  * **Depth bound.** ``max_depth`` bounds the reachability walk *from the
    loop-nested callee* to the sink (per the design); the loop owner ``A`` is
    one further labelling hop in the reported path, not a counted reachability
    hop. Chains longer than that from the callee are not followed.
  * **First-hop name match.** The loop-nested callee is matched to one of the
    loop owner's *resolved* graph callees by name. The resolver did the real
    binding; the name only selects which of the owner's calls were in a loop.
    Two distinct callees sharing a name under one function (rare) could let an
    out-of-loop edge stand in for the in-loop one.
  * **Incremental runs.** When only a subset of files is walked, a sink-holding
    callee that was not itself re-walked is absent from the sink set; full
    detection requires a whole-repo analyze (the index recomputes there).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .callgraph import CallGraphIndex
from .reachability import path_to_sink, reachable_to_sink

if TYPE_CHECKING:
    # Imported lazily at runtime (see ``_hits_for_function``) to avoid a cycle:
    # the walker pulls in ``perf.io_boundaries`` while the ``complexity``
    # package is still initialising, so ``crossfn`` cannot import it eagerly.
    from ..complexity import FileComplexity, PerfFnFacts, PerfHit

# Cross-function hits carry this so the biomarker can phrase them distinctly
# while still scoring under the same ``io_in_loop`` / ``performance`` budget.
CROSSFN_KIND = "io_in_loop"


def collect_crossfn_io_in_loop(
    walked: Iterable[tuple[Any, FileComplexity]],
    graph: Any,
    *,
    index: CallGraphIndex | None = None,
    max_depth: int = 3,
) -> dict[str, list[PerfHit]]:
    """Cross-function ``io_in_loop`` hits, keyed by the loop-owning file path.

    ``walked`` is the engine's ``(parsed_file, FileComplexity)`` list; ``graph``
    is the resolved dependency graph (file + symbol nodes with ``calls``
    edges), or ``None``. ``index`` is an optional pre-built
    :class:`CallGraphIndex` (the engine builds one and shares it across the
    graph passes); when omitted it is built from ``graph``. Each returned
    :class:`PerfHit` carries the sink's boundary kind in ``detail`` and the
    ``A -> ... -> sink`` symbol path in ``path`` (non-empty ``path`` is what
    marks a hit as cross-function).
    """
    walked_list = list(walked)
    if graph is None or not walked_list:
        return {}

    # Cheap pre-checks over the already-computed facts, before touching the
    # graph at all: a cross-function N+1 needs BOTH a function holding a bare
    # sink (a reachability target) and a function with a loop-nested call (an
    # entry). On a repo with neither, this returns without scanning the graph.
    has_sink = any(
        fact.bare_sink_kind is not None for _pf, fcx in walked_list for fact in fcx.perf_fn_facts
    )
    has_entry = any(
        fact.loop_call_targets for _pf, fcx in walked_list for fact in fcx.perf_fn_facts
    )
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

    # --- match each loop owner's loop-nested callees against the reach map ----
    out: dict[str, list[PerfHit]] = {}
    for pf, fcx in walked_list:
        path = pf.file_info.path
        for fact in fcx.perf_fn_facts:
            if not fact.loop_call_targets:
                continue
            hits = _hits_for_function(path, fact, index, reach, sink_kind)
            if hits:
                out.setdefault(path, []).extend(hits)
    return out


def _hits_for_function(
    path: str,
    fact: PerfFnFacts,
    index: CallGraphIndex,
    reach: dict[str, Any],
    sink_kind: dict[str, str],
) -> list[PerfHit]:
    from ..complexity import PerfHit

    a_sid = index.resolve_function(path, fact.func_start)
    if a_sid is None:
        return []
    callees = index.forward.get(a_sid)
    if not callees:
        return []
    # Index the owner's resolved callees by name for the first-hop match.
    callees_by_name: dict[str, list[str]] = {}
    for c in callees:
        callees_by_name.setdefault(index.name.get(c, ""), []).append(c)

    hits: list[PerfHit] = []
    seen: set[str] = set()
    for target_name, call_line in fact.loop_call_targets:
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
                PerfHit(
                    kind=CROSSFN_KIND,
                    line=call_line,
                    function=fact.function,
                    detail=kind,
                    path=(a_sid, *chain),
                )
            )
            break
    return hits
