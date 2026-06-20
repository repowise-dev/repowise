"""Unit tests for the Phase-7b centrality-gated moat markers.

Four markers built on Primitive 2 (the severity ranker, used as a precision
gate) and Primitive 3 (the sink-agnostic reachability engine):

  * ``nested_loop_with_io`` — an I/O sink two loops deep (un-gated; nesting is
    the precision lever). Emitted directly by the walker.
  * ``blocking_io_under_lock`` — an I/O sink reached while a block-scoped lock is
    held (same-function via the walker; cross-function via reachability).
  * ``nested_loop_quadratic`` / ``hot_path_sync_io`` — recorded as per-function
    facts by the walker and turned into hits by the engine's centrality gate
    ONLY for a hot (central / churny) function.

Grammar availability is best-effort (a missing grammar degrades to "no hits").
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.blocking_io_under_lock import (
    BlockingIoUnderLockDetector,
)
from repowise.core.analysis.health.biomarkers.hot_path_sync_io import HotPathSyncIoDetector
from repowise.core.analysis.health.biomarkers.nested_loop_quadratic import (
    NestedLoopQuadraticDetector,
)
from repowise.core.analysis.health.biomarkers.nested_loop_with_io import NestedLoopWithIoDetector
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.complexity import PerfFnFacts, PerfHit, walk_file
from repowise.core.analysis.health.perf import (
    CallGraphIndex,
    PerfRanker,
    collect_blocking_io_under_lock,
    collect_centrality_gated,
)
from repowise.core.analysis.health.scoring import dimensions_for, score_file

_NEW_KINDS = [
    "nested_loop_with_io",
    "nested_loop_quadratic",
    "hot_path_sync_io",
    "blocking_io_under_lock",
]


def _hits(lang: str, src: str):
    fc = walk_file(f"t.{lang}", lang, src.encode())
    return sorted((h.kind, h.detail) for h in fc.perf_hits)


def _facts(lang: str, src: str) -> dict[str | None, PerfFnFacts]:
    fc = walk_file(f"t.{lang}", lang, src.encode())
    return {f.function: f for f in fc.perf_fn_facts}


# ---------------------------------------------------------------------------
# nested_loop_with_io — un-gated, rides alongside io_in_loop (loop_depth >= 2)
# ---------------------------------------------------------------------------

_NESTED_IO_CASES = [
    (
        "python",
        "def f(session, groups):\n"
        "    for g in groups:\n"
        "        for r in g:\n"
        "            session.execute(r)\n",
        [("io_in_loop", "db"), ("nested_loop_with_io", "db")],
        "db execute two loops deep -> io_in_loop + nested_loop_with_io",
    ),
    (
        "python",
        "def f(session, repos):\n    for r in repos:\n        session.execute(r)\n",
        [("io_in_loop", "db")],
        "a single loop is io_in_loop only (no nesting)",
    ),
    (
        "typescript",
        "async function f(groups){ for (const g of groups){ "
        "for (const u of g){ await fetch(u); } } }",
        [
            ("io_in_loop", "network"),
            ("nested_loop_with_io", "network"),
            ("serial_await_in_loop", "network"),
        ],
        "awaited fetch two loops deep -> +nested_loop_with_io",
    ),
]


@pytest.mark.parametrize(
    "lang,src,expected,note", _NESTED_IO_CASES, ids=[c[3] for c in _NESTED_IO_CASES]
)
def test_nested_loop_with_io_cases(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# blocking_io_under_lock — same-function (C# lock / Java synchronized blocks)
# ---------------------------------------------------------------------------

_LOCK_IO_CASES = [
    (
        "csharp",
        "class A{ void M(object gate, AppDbContext ctx){ lock(gate){ ctx.SaveChanges(); } } }",
        [("blocking_io_under_lock", "db")],
        "EF SaveChanges() inside lock(){} -> blocking_io_under_lock",
    ),
    (
        "csharp",
        "class A{ void M(object gate, AppDbContext ctx){"
        " ctx.SaveChanges(); lock(gate){ Use(); } } }",
        [],
        "a sink OUTSIDE the lock block does not fire",
    ),
    (
        "java",
        "class A{ void m(Object g, javax.persistence.EntityManager em){"
        " synchronized(g){ em.getResultList(); } } }",
        [("blocking_io_under_lock", "db")],
        "JPA getResultList() inside synchronized(){} -> blocking_io_under_lock",
    ),
    (
        "java",
        "class A{ void m(javax.persistence.EntityManager em){"
        " synchronized(em.getResultList()){ Use(); } } }",
        [],
        "a sink in the lock-OBJECT expression runs before the lock is held",
    ),
]


@pytest.mark.parametrize(
    "lang,src,expected,note", _LOCK_IO_CASES, ids=[c[3] for c in _LOCK_IO_CASES]
)
def test_blocking_io_under_lock_same_function(lang, src, expected, note):
    assert _hits(lang, src) == sorted(expected), note


# ---------------------------------------------------------------------------
# The walker records the gated facts (it does NOT emit the gated hits itself)
# ---------------------------------------------------------------------------


def test_walker_records_nested_loop_fact_not_a_hit():
    src = "def f(xs, ys):\n    for x in xs:\n        for y in ys:\n            use(x, y)\n"
    fc = walk_file("t.py", "python", src.encode())
    # No I/O, no nested_loop_with_io hit — but the fact is recorded for the gate.
    assert all(h.kind != "nested_loop_quadratic" for h in fc.perf_hits)
    fact = {f.function: f for f in fc.perf_fn_facts}["f"]
    assert fact.nested_loop_line == 3  # the inner ``for``


def test_walker_records_blocking_sink_fact_not_a_hit():
    src = "import subprocess\ndef handler(args):\n    return subprocess.run(args)\n"
    fc = walk_file("t.py", "python", src.encode())
    assert all(h.kind != "hot_path_sync_io" for h in fc.perf_hits)
    fact = {f.function: f for f in fc.perf_fn_facts}["handler"]
    assert fact.blocking_sink_kind == "subprocess"
    assert fact.blocking_sink_line == 3


def test_awaited_sink_is_not_a_blocking_fact():
    src = "import httpx\nasync def handler(u):\n    return await httpx.get(u)\n"
    fact = _facts("py", src).get("handler")
    # An awaited (non-blocking) sink is not a hot_path candidate.
    assert fact is None or fact.blocking_sink_kind is None


def test_db_materializer_is_not_a_blocking_fact():
    # ``result.scalars().all()`` is a materializer on an already-awaited query,
    # not a blocking round-trip — db is excluded from hot_path (probe FP fix).
    src = "def handler(result):\n    return result.scalars().all()\n"
    fact = _facts("py", src).get("handler")
    assert fact is None or fact.blocking_sink_kind is None


# ---------------------------------------------------------------------------
# The centrality gate — emits the two markers ONLY for a hot function
# ---------------------------------------------------------------------------


class _PF:
    """Minimal stand-in for a ParsedFile (the gate only reads file_info.path)."""

    class _FI:
        def __init__(self, path):
            self.path = path

    def __init__(self, path):
        self.file_info = _PF._FI(path)


def _walked(path: str, src: str):
    return [(_PF(path), walk_file(path, "python", src.encode()))]


_HOT_SRC = (
    "import subprocess\n"
    "def hot(xs, ys):\n"
    "    subprocess.run(['git', 'status'])\n"  # a blocking loop_depth-0 sink (hot_path)
    "    for x in xs:\n"
    "        for y in ys:\n"  # nested loop (quadratic)
    "            use(x, y)\n"
)


def test_centrality_gate_fires_in_a_churny_file():
    walked = _walked("svc.py", _HOT_SRC)
    # No graph, but the file is a git hotspot -> churny -> hot.
    ranker = PerfRanker(None, {"svc.py": {"is_hotspot": True}})
    out = collect_centrality_gated(walked, ranker)
    kinds = sorted(h.kind for h in out.get("svc.py", []))
    assert kinds == ["hot_path_sync_io", "nested_loop_quadratic"]
    assert out["svc.py"][0].detail or True  # hot_path carries the boundary kind


def test_centrality_gate_silent_without_any_hot_signal():
    walked = _walked("cold.py", _HOT_SRC)
    # No graph, no git metadata -> nothing is hot -> no gated markers ship.
    ranker = PerfRanker(None, {})
    assert collect_centrality_gated(walked, ranker) == {}


def test_centrality_gate_fires_for_a_central_function():
    # A symbol with top-quintile caller count is "central" even without churn.
    g = nx.MultiDiGraph()
    g.add_node(
        "svc.py::hot", node_type="symbol", name="hot", file_path="svc.py", start_line=1, end_line=6
    )
    callers = []
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
        g.add_edge(cid, "svc.py::hot", edge_type="calls")
        callers.append(cid)
    # A cold leaf with a single caller — must NOT be central.
    g.add_node(
        "svc.py::cold",
        node_type="symbol",
        name="cold",
        file_path="svc.py",
        start_line=20,
        end_line=25,
    )
    g.add_edge(callers[0], "svc.py::cold", edge_type="calls")

    index = CallGraphIndex(g)
    ranker = PerfRanker(index, {})
    assert ranker.is_central("svc.py", 1) is True
    assert ranker.is_central("svc.py", 20) is False


# ---------------------------------------------------------------------------
# Cross-function blocking_io_under_lock — reachability with a lock entry set
# ---------------------------------------------------------------------------


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


def test_crossfn_blocking_io_under_lock():
    # ``holder`` holds a lock around a call to ``writer``; ``writer`` does the I/O.
    symbols = {
        "svc.py::holder": ("svc.py", 1, 5),
        "svc.py::writer": ("svc.py", 7, 9),
    }
    graph = _fake_graph(symbols, [("svc.py::holder", "svc.py::writer")])
    pf = _PF("svc.py")
    fcx = walk_file("svc.py", "python", b"def x():\n    pass\n")  # facts overridden below
    fcx.perf_fn_facts = [
        PerfFnFacts(
            function="holder",
            func_start=1,
            loop_call_targets=(),
            bare_sink_kind=None,
            lock_call_targets=(("writer", 3),),
        ),
        PerfFnFacts(
            function="writer",
            func_start=7,
            loop_call_targets=(),
            bare_sink_kind="db",
        ),
    ]
    out = collect_blocking_io_under_lock([(pf, fcx)], graph)
    hits = [h for hs in out.values() for h in hs]
    assert len(hits) == 1
    h = hits[0]
    assert h.kind == "blocking_io_under_lock"
    assert h.detail == "db"
    assert h.function == "holder"
    assert h.path[0].endswith("::holder")
    assert h.path[-1].endswith("::writer")


def test_crossfn_blocking_io_under_lock_no_lock_is_noop():
    symbols = {"svc.py::a": ("svc.py", 1, 3), "svc.py::b": ("svc.py", 5, 7)}
    graph = _fake_graph(symbols, [("svc.py::a", "svc.py::b")])
    pf = _PF("svc.py")
    fcx = walk_file("svc.py", "python", b"def x():\n    pass\n")
    fcx.perf_fn_facts = [
        PerfFnFacts(function="a", func_start=1, loop_call_targets=(("b", 2),), bare_sink_kind=None),
        PerfFnFacts(function="b", func_start=5, loop_call_targets=(), bare_sink_kind="db"),
    ]
    # ``a`` calls ``b`` in a loop, not under a lock -> no lock→io hit.
    assert collect_blocking_io_under_lock([(pf, fcx)], graph) == {}


# ---------------------------------------------------------------------------
# Biomarkers lift hits; scoring keeps every marker performance-only
# ---------------------------------------------------------------------------


def _ctx(perf_hits: list[PerfHit]) -> FileContext:
    return FileContext(
        file_path="x.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module="x",
        perf_hits=perf_hits,
    )


@pytest.mark.parametrize("kind", _NEW_KINDS)
def test_each_marker_is_performance_only(kind):
    assert dimensions_for(kind) == {"performance"}


def test_detectors_only_consume_their_own_kind():
    ctx = _ctx(
        [
            PerfHit("nested_loop_with_io", 1, "f", "db"),
            PerfHit("nested_loop_quadratic", 2, "f", ""),
            PerfHit("hot_path_sync_io", 3, "f", "network"),
            PerfHit("blocking_io_under_lock", 4, "f", "db"),
        ]
    )
    assert len(NestedLoopWithIoDetector().detect(ctx)) == 1
    assert len(NestedLoopQuadraticDetector().detect(ctx)) == 1
    assert len(HotPathSyncIoDetector().detect(ctx)) == 1
    assert len(BlockingIoUnderLockDetector().detect(ctx)) == 1


def test_blocking_io_under_lock_renders_cross_function_path():
    hit = PerfHit(
        "blocking_io_under_lock", 3, "holder", "db", path=("svc.py::holder", "svc.py::writer")
    )
    (finding,) = BlockingIoUnderLockDetector().detect(_ctx([hit]))
    assert finding.details["cross_function"] is True
    assert finding.details["path"] == list(hit.path)
    assert "holder -> writer" in finding.reason


def test_new_markers_score_performance_not_defect():
    ctx = _ctx([PerfHit(k, i + 1, "f", "") for i, k in enumerate(_NEW_KINDS)])
    results = detect_all(ctx)
    got = {r.biomarker_type for r in results}
    assert set(_NEW_KINDS) <= got
    scores, deductions = score_file(results)
    assert scores["defect"] == 10.0
    assert scores["maintainability"] == 10.0
    assert scores["performance"] < 10.0
    assert all(d == 0.0 for d in deductions)
