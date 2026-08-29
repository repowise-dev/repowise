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
  * the sink-agnostic :mod:`.reachability` walk.

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
  * **First-hop compatibility.** New indexes retain the resolved call's source
    line, so the loop-nested call selects the exact graph edge. Indexes created
    before that fact existed fall back to matching the owner's resolved callees
    by name until the next refresh.
  * **Incremental runs.** When only a subset of files is walked, a sink-holding
    callee that was not itself re-walked is absent from the sink set; full
    detection requires a whole-repo analyze (the index recomputes there).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .callgraph import CallGraphIndex
from .sink_reach import collect_sink_reaching_hits

if TYPE_CHECKING:
    from ..complexity import FileComplexity, PerfHit

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
    :class:`PerfHit` carries the sink's boundary kind in ``detail``, the
    ``A -> ... -> sink`` symbol path in ``path`` (non-empty ``path`` is what
    marks a hit as cross-function), and the ``resolution_basis`` the index
    used to select the exact first-hop edge.

    The walk itself lives in :func:`.sink_reach.collect_sink_reaching_hits`,
    shared with the lock-held-I/O pass; only the entry set and the hit kind
    differ between the two.
    """
    return collect_sink_reaching_hits(
        walked,
        graph,
        entries=lambda fact: fact.loop_call_targets,
        kind=CROSSFN_KIND,
        index=index,
        max_depth=max_depth,
    )
