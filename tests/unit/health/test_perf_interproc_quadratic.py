"""Unit tests for the cross-function ``interprocedural_quadratic_loop`` machinery.

A loop in function ``A`` calls a helper ``B`` that, within a few call hops, has
its OWN data-dependent loop. The walker records each function's own loop
(``PerfFnFacts.own_loop_line``) and ``collect_interprocedural_quadratic`` finds
the cross-function pairs via the sink-agnostic reachability engine, centrality-
gated on the loop owner.

This validated at ~2% precision (see ``local-stash/performance-pillar/
PROBE_FINDINGS.md``) so it is NOT wired into the engine and produces no finding.
These tests lock the reusable infrastructure (the walker fact + the collector),
which is kept for a future interprocedural-dataflow-gated detector.

Grammar availability is best-effort (a missing grammar degrades to "no hits").
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.complexity import PerfFnFacts, walk_file
from repowise.core.analysis.health.perf import (
    PerfRanker,
    collect_interprocedural_quadratic,
)

_KIND = "interprocedural_quadratic_loop"


class _PF:
    """Minimal stand-in for a ParsedFile (the pass only reads file_info.path)."""

    class _FI:
        def __init__(self, path):
            self.path = path

    def __init__(self, path):
        self.file_info = _PF._FI(path)


def _fake_graph(symbols: dict[str, tuple[str, int, int]], calls: list[tuple[str, str]]):
    """Build a tiny resolved-calls DiGraph. ``symbols`` maps id -> (path, start, end)."""
    g = nx.MultiDiGraph()
    for sid, (path, start, end) in symbols.items():
        g.add_node(
            sid,
            node_type="symbol",
            name=sid.rsplit("::", 1)[-1],
            file_path=path,
            start_line=start,
            end_line=end,
        )
    for src, dst in calls:
        g.add_edge(src, dst, edge_type="calls")
    return g


# ---------------------------------------------------------------------------
# The walker records each function's own data-dependent loop as a fact.
# ---------------------------------------------------------------------------


def test_walker_records_own_loop_line():
    src = "def inner(rows):\n    for r in rows:\n        use(r)\n"
    fc = walk_file("t.py", "python", src.encode())
    fact = {f.function: f for f in fc.perf_fn_facts}["inner"]
    assert fact.own_loop_line == 2  # the ``for`` line


def test_walker_skips_constant_loop_for_own_loop():
    # A loop over a fixed literal / range is not data-dependent -> not a target.
    src = "def inner():\n    for r in range(10):\n        use(r)\n"
    fc = walk_file("t.py", "python", src.encode())
    fact = {f.function: f for f in fc.perf_fn_facts}.get("inner")
    assert fact is None or fact.own_loop_line == 0


def test_walker_no_loop_no_own_loop_fact():
    src = "def inner(x):\n    return x + 1\n"
    fc = walk_file("t.py", "python", src.encode())
    fact = {f.function: f for f in fc.perf_fn_facts}.get("inner")
    assert fact is None or fact.own_loop_line == 0


# ---------------------------------------------------------------------------
# Cross-function detection, end-to-end over a real walk + a resolved graph.
# ---------------------------------------------------------------------------

_QUAD_SRC = (
    "def outer(items):\n"
    "    for it in items:\n"
    "        inner(it)\n"  # loop-nested call to a looping helper
    "def inner(x):\n"
    "    for y in x:\n"
    "        use(y)\n"
)


def _walked_quad(path="svc.py"):
    pf = _PF(path)
    fcx = walk_file(path, "python", _QUAD_SRC.encode())
    # outer defined at line 1, inner at line 4 (1-indexed def lines).
    graph = _fake_graph(
        {f"{path}::outer": (path, 1, 3), f"{path}::inner": (path, 4, 6)},
        [(f"{path}::outer", f"{path}::inner")],
    )
    return [(pf, fcx)], graph, pf


def test_crossfn_quadratic_fires_in_a_churny_file():
    walked, graph, _pf = _walked_quad()
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})  # churny -> hot
    out = collect_interprocedural_quadratic(walked, graph, ranker)
    hits = [h for hs in out.values() for h in hs]
    assert len(hits) == 1
    h = hits[0]
    assert h.kind == _KIND
    assert h.function == "outer"
    assert h.path[0].endswith("::outer")
    assert h.path[-1].endswith("::inner")


def test_crossfn_quadratic_silent_without_a_hot_signal():
    walked, graph, _pf = _walked_quad()
    # No graph centrality, no git metadata -> nothing is hot -> no hits.
    ranker = PerfRanker(None, {})
    assert collect_interprocedural_quadratic(walked, graph, ranker) == {}


def test_crossfn_quadratic_fires_for_a_central_loop_owner():
    walked, _graph, _pf = _walked_quad()
    # Give ``outer`` top-quintile caller count so it is central without churn.
    g = _fake_graph(
        {"svc.py::outer": ("svc.py", 1, 3), "svc.py::inner": ("svc.py", 4, 6)},
        [("svc.py::outer", "svc.py::inner")],
    )
    for i in range(4):
        cid = f"c.py::caller{i}"
        g.add_node(
            cid,
            node_type="symbol",
            name=f"caller{i}",
            file_path="c.py",
            start_line=10 + i,
            end_line=11 + i,
        )
        g.add_edge(cid, "svc.py::outer", edge_type="calls")
    from repowise.core.analysis.health.perf import CallGraphIndex

    index = CallGraphIndex(g)
    ranker = PerfRanker(index, {})
    out = collect_interprocedural_quadratic(walked, g, ranker, index=index)
    hits = [h for hs in out.values() for h in hs]
    assert len(hits) == 1
    assert hits[0].function == "outer"


def test_crossfn_quadratic_no_hit_when_helper_has_no_loop():
    # ``inner`` does not loop -> not a reachability target -> no quadratic hit.
    src = (
        "def outer(items):\n"
        "    for it in items:\n"
        "        inner(it)\n"
        "def inner(x):\n"
        "    return x + 1\n"
    )
    pf = _PF("svc.py")
    fcx = walk_file("svc.py", "python", src.encode())
    graph = _fake_graph(
        {"svc.py::outer": ("svc.py", 1, 3), "svc.py::inner": ("svc.py", 4, 5)},
        [("svc.py::outer", "svc.py::inner")],
    )
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})
    assert collect_interprocedural_quadratic([(pf, fcx)], graph, ranker) == {}


def test_crossfn_quadratic_skips_self_recursion():
    # ``outer`` loops and calls itself: the only reached loop is its own, so the
    # self-loop degenerate case must not produce a hit.
    symbols = {"svc.py::outer": ("svc.py", 1, 3)}
    graph = _fake_graph(symbols, [("svc.py::outer", "svc.py::outer")])
    pf = _PF("svc.py")
    fcx = walk_file("svc.py", "python", b"def x():\n    pass\n")
    fcx.perf_fn_facts = [
        PerfFnFacts(
            function="outer",
            func_start=1,
            loop_call_targets=(("outer", 2),),
            bare_sink_kind=None,
            own_loop_line=2,
        ),
    ]
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})
    assert collect_interprocedural_quadratic([(pf, fcx)], graph, ranker) == {}


def test_crossfn_quadratic_no_graph_is_noop():
    walked, _graph, _pf = _walked_quad()
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})
    assert collect_interprocedural_quadratic(walked, None, ranker) == {}


def test_collector_emits_its_own_kind():
    walked, graph, _pf = _walked_quad()
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})
    out = collect_interprocedural_quadratic(walked, graph, ranker)
    hits = [h for hs in out.values() for h in hs]
    assert hits and all(h.kind == _KIND for h in hits)


def test_marker_is_not_registered_as_a_biomarker():
    # Deliberately unwired: no biomarker consumes the kind and it is absent from
    # the scoring tables, so it can never reach a finding or move any dimension.
    from repowise.core.analysis.health.biomarkers.registry import registered_biomarkers
    from repowise.core.analysis.health.scoring import dimensions_for

    assert _KIND not in {b.name for b in registered_biomarkers()}
    # Unlisted -> defaults to the historical single {"defect"}; never emitted.
    assert dimensions_for(_KIND) == {"defect"}
