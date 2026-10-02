"""Centrality-gated markers.

Two passes that run after the walker, over the resolved ``calls`` graph, using
the :class:`~.ranking.PerfRanker` and the sink-agnostic reachability engine:

  * :func:`collect_centrality_gated` — turns the walker's per-function *facts*
    (``PerfFnFacts.nested_loop_line`` / ``blocking_sink_{kind,line}``) into
    ``nested_loop_quadratic`` and ``hot_path_sync_io`` hits, but ONLY for a
    function the ranker calls *hot* (top-quintile call-graph centrality or a
    churny/hotspot file). The shapes are unambiguous but noisy when flagged
    everywhere; the gate is what makes the O(n^2) marker precise enough to
    emit. Keeping the generation here (not in the walker) leaves the raw walker
    output the same high-precision same-function set it has always been.

  * :func:`collect_blocking_io_under_lock` — the cross-function lock→I/O case: a
    function holds a lock around a call to a helper that, within a few hops,
    executes an I/O boundary. Pure moat: it reuses the identical reverse-BFS the
    cross-function N+1 pass uses, only the *entry set* differs (callees invoked
    while a lock is held, ``PerfFnFacts.lock_call_targets``, instead of every
    loop-nested callee).

Both are failure-isolated by their callers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from repowise.core.code_origin import code_origin, is_build_file
from repowise.core.support_paths import is_example_path

from .callgraph import CallGraphIndex
from .sink_reach import collect_sink_reaching_hits

if TYPE_CHECKING:
    from ..complexity import FileComplexity, PerfHit
    from .ranking import PerfRanker

# The boundary-kind detail convention shared with the cross-function N+1 pass.
LOCK_IO_KIND = "blocking_io_under_lock"

# Origins whose code serves no request: tests, tooling (scripts, CI,
# benchmarks, migrations), docs examples, and code nobody here maintains by
# hand. A blocking call there is how the job is done, so it never becomes a
# ``hot_path_sync_io`` finding however many callers it has. A CLI is product
# code (``code_origin`` calls it production) and still fires.
_NON_SERVING_ORIGINS = frozenset({"test", "tooling", "docs_example", "generated", "vendored"})


def _may_serve_requests(path: str, origins: Mapping[str, str] | None) -> bool:
    """Whether code at *path* can sit on a path that serves requests.

    *origins* is the content-aware ``code_origin`` the health pass already
    decided per file; a path missing from it falls back to the path-only answer.
    An example tree at any depth is excluded too: ``code_origin`` counts only a
    root-level one as ``docs_example``, but a nested ``examples/`` shows how to
    use the code rather than being it.
    """
    origin = (origins or {}).get(path) or code_origin(path)
    return origin not in _NON_SERVING_ORIGINS and not is_example_path(path)


def _in_rust_test_range(line: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """True when *line* falls inside one of *ranges* (Rust test-only spans).

    *ranges* is ``FileComplexity.rust_test_line_ranges``, computed once per
    file by the walker from the tree it already parsed (see
    ``complexity.file_scan.scan_file``) — empty for every language
    but Rust, so this is a no-op everywhere else. Checked here, at the single
    choke point both centrality-gated markers share, so a function inside a
    ``#[cfg(test)] mod`` / ``#[test]`` fn — invisible to the file-level
    ``is_test`` heuristic — never emits ``hot_path_sync_io`` or
    ``nested_loop_quadratic``, without weakening the gate for production code.
    """
    return any(start <= line <= end for start, end in ranges)


def collect_centrality_gated(
    walked: Iterable[tuple[Any, FileComplexity]],
    ranker: PerfRanker,
    origins: Mapping[str, str] | None = None,
) -> dict[str, list[PerfHit]]:
    """Centrality-gated ``nested_loop_quadratic`` / ``hot_path_sync_io`` hits.

    Keyed by file path. For every function carrying the corresponding fact, a hit
    is produced ONLY when ``ranker.is_hot(path, func_start)``, so a quadratic
    loop or a blocking sync sink ships only in one of the repo's most-called
    functions. Pure when no graph is available (nothing is hot, so no hits),
    which is the precision-first default. A fact whose function
    sits inside Rust inline test code (``_in_rust_test_range``) is skipped
    before the hotness check even runs — test code doing blocking I/O is
    normal, not a finding, regardless of how central or churny its file is.
    A build file is never a hot path either: the build tool runs it, no
    request does. The same holds for a blocking sink in any file that serves
    no request (:func:`_may_serve_requests`).
    """
    from ..complexity import PerfHit

    out: dict[str, list[PerfHit]] = {}
    for pf, fcx in walked:
        if not fcx.perf_fn_facts:
            continue
        path = pf.file_info.path
        if is_build_file(path):
            continue
        file_hits: list[PerfHit] = []
        test_ranges = fcx.rust_test_line_ranges
        for fact in fcx.perf_fn_facts:
            if fact.nested_loop_line == 0 and fact.blocking_sink_kind is None:
                continue
            if test_ranges and _in_rust_test_range(fact.func_start, test_ranges):
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
            if fact.blocking_sink_kind is not None and _may_serve_requests(path, origins):
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
    an I/O boundary within ``max_depth`` hops -- the I/O round-trip runs under the
    lock, serializing every thread on a network/db/fs wait. The same-function case
    (an I/O sink lexically inside a ``lock``/``synchronized`` block) is emitted
    directly by the walker.

    The walk is :func:`.sink_reach.collect_sink_reaching_hits`, shared with
    ``crossfn.collect_crossfn_io_in_loop``: the only difference is the entry set
    is ``PerfFnFacts.lock_call_targets`` (callees invoked under a held lock)
    instead of loop-nested callees.
    """
    return collect_sink_reaching_hits(
        walked,
        graph,
        entries=lambda fact: fact.lock_call_targets,
        kind=LOCK_IO_KIND,
        index=index,
        max_depth=max_depth,
        carry_func_start=True,
    )
