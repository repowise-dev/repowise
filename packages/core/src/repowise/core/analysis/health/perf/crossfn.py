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

from .reachability import path_to_sink, reachable_to_sink

if TYPE_CHECKING:
    # Imported lazily at runtime (see ``_hits_for_function``) to avoid a cycle:
    # the walker pulls in ``perf.io_boundaries`` while the ``complexity``
    # package is still initialising, so ``crossfn`` cannot import it eagerly.
    from ..complexity import FileComplexity, PerfFnFacts, PerfHit

# Cross-function hits carry this so the biomarker can phrase them distinctly
# while still scoring under the same ``io_in_loop`` / ``performance`` budget.
CROSSFN_KIND = "io_in_loop"


def _module_node_id(path: str) -> str:
    return f"{path}::__module__"


class _CallGraphIndex:
    """One-pass index of the dependency graph for the cross-function pass.

    Holds only what the pass needs: symbol-node name lookup, a ``calls``
    adjacency in both directions, and a resolver from ``(file, def-line)`` to a
    symbol-node id. Built once; never mutates the source graph.
    """

    __slots__ = ("_by_file_line", "_ranges", "forward", "name", "nodes", "reverse")

    def __init__(self, graph: Any) -> None:
        self.nodes: set[str] = set()
        self.name: dict[str, str] = {}
        self.forward: dict[str, list[str]] = {}
        self.reverse: dict[str, list[str]] = {}
        # Exact (path, start_line) -> symbol id, plus a per-file range list for
        # the containment fallback when a def line doesn't match exactly.
        self._by_file_line: dict[tuple[str, int], str] = {}
        self._ranges: dict[str, list[tuple[int, int, str]]] = {}

        for node_id, attrs in graph.nodes(data=True):
            self.nodes.add(node_id)
            if attrs.get("node_type") != "symbol":
                continue
            self.name[node_id] = attrs.get("name") or ""
            path = attrs.get("file_path")
            if not path:
                continue
            start = attrs.get("start_line")
            end = attrs.get("end_line")
            if isinstance(start, int):
                self._by_file_line.setdefault((path, start), node_id)
                if isinstance(end, int):
                    self._ranges.setdefault(path, []).append((start, end, node_id))

        for src, dst, data in graph.edges(data=True):
            if data.get("edge_type") != "calls":
                continue
            self.forward.setdefault(src, []).append(dst)
            self.reverse.setdefault(dst, []).append(src)

    def resolve_function(self, path: str, func_start: int) -> str | None:
        """Symbol-node id for the function defined at ``func_start`` in ``path``.

        ``func_start == 0`` is module scope (the synthetic ``::__module__``
        node). Otherwise prefer an exact def-line match, then fall back to the
        innermost symbol whose line range contains ``func_start`` (tolerates a
        decorator/def off-by-one without matching an enclosing scope).
        """
        if func_start == 0:
            mod = _module_node_id(path)
            return mod if mod in self.nodes else None
        exact = self._by_file_line.get((path, func_start))
        if exact is not None:
            return exact
        best: str | None = None
        best_start = -1
        for start, end, node_id in self._ranges.get(path, ()):
            if start <= func_start <= end and start > best_start:
                best, best_start = node_id, start
        return best


def collect_crossfn_io_in_loop(
    walked: Iterable[tuple[Any, FileComplexity]],
    graph: Any,
    *,
    max_depth: int = 3,
) -> dict[str, list[PerfHit]]:
    """Cross-function ``io_in_loop`` hits, keyed by the loop-owning file path.

    ``walked`` is the engine's ``(parsed_file, FileComplexity)`` list; ``graph``
    is the resolved dependency graph (file + symbol nodes with ``calls``
    edges), or ``None``. Each returned :class:`PerfHit` carries the sink's
    boundary kind in ``detail`` and the ``A -> ... -> sink`` symbol path in
    ``path`` (non-empty ``path`` is what marks a hit as cross-function).
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

    index = _CallGraphIndex(graph)
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
    index: _CallGraphIndex,
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
