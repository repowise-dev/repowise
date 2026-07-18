"""Cross-function io-in-loop / N+1 (PR4 — the Tier-B moat).

Three layers:

  * ``reachability`` — the sink-agnostic bounded BFS in isolation.
  * the walker's per-function facts (``loop_call_targets`` / ``bare_sink_kind``).
  * the end-to-end bridge over a real resolved ``calls`` graph, including the
    cross-*file* case, plus the guardrails (same-function hits unchanged, the
    defect score untouched).

Like the other walker tests, tree-sitter grammar availability is best-effort.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.io_in_loop import IoInLoopDetector
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.complexity import PerfHit, walk_file
from repowise.core.analysis.health.perf import (
    collect_crossfn_io_in_loop,
    path_to_sink,
    reachable_to_sink,
)
from repowise.core.analysis.health.scoring import score_file
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

# ---------------------------------------------------------------------------
# reachability — the sink-agnostic primitive
# ---------------------------------------------------------------------------


def _adj(edges: dict[str, list[str]]):
    """Build a ``predecessors`` callable from a forward edge map.

    Reachability walks edges *backwards* from sinks, so it consumes the reverse
    adjacency: for a forward edge ``a -> b`` it must yield ``a`` from ``b``.
    """
    reverse: dict[str, list[str]] = {}
    for src, dsts in edges.items():
        for d in dsts:
            reverse.setdefault(d, []).append(src)
    return lambda node: reverse.get(node, ())


def test_reachability_direct_sink():
    info = reachable_to_sink({"sink"}, _adj({}), max_depth=3)
    assert info["sink"].distance == 0
    assert info["sink"].next_hop is None
    assert path_to_sink("sink", info) == ["sink"]


def test_reachability_chain_within_depth():
    # a -> b -> c -> sink (3 hops)
    fwd = {"a": ["b"], "b": ["c"], "c": ["sink"]}
    info = reachable_to_sink({"sink"}, _adj(fwd), max_depth=3)
    assert info["a"].distance == 3
    assert path_to_sink("a", info) == ["a", "b", "c", "sink"]


def test_reachability_respects_depth_bound():
    # a -> b -> c -> d -> sink (4 hops) — beyond a depth-3 bound from ``a``.
    fwd = {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["sink"]}
    info = reachable_to_sink({"sink"}, _adj(fwd), max_depth=3)
    assert "a" not in info  # 4 hops > 3
    assert info["b"].distance == 3
    assert "sink" in info


def test_reachability_unreachable_node_absent():
    fwd = {"a": ["b"], "lonely": ["other"]}
    info = reachable_to_sink({"sink"}, _adj(fwd), max_depth=3)
    assert info == {"sink": info["sink"]}
    assert path_to_sink("lonely", info) == []


def test_reachability_records_nearest_sink():
    # a reaches s1 in 1 hop and s2 in 2 hops → nearest is s1.
    fwd = {"a": ["s1"], "s1": ["s2"]}
    info = reachable_to_sink({"s1", "s2"}, _adj(fwd), max_depth=3)
    assert info["a"].sink == "s1"
    assert info["a"].distance == 1


# ---------------------------------------------------------------------------
# walker per-function facts
# ---------------------------------------------------------------------------


def _facts(source: bytes, lang: str = "python"):
    return {f.function: f for f in walk_file(f"t.{lang[:2]}", lang, source).perf_fn_facts}


def test_walker_records_bare_sink_kind():
    src = (
        b"from sqlalchemy import select\n"
        b"def helper(session, r):\n"
        b"    return session.execute(select(r))\n"
    )
    facts = _facts(src)
    assert facts["helper"].bare_sink_kind == "db"
    assert facts["helper"].loop_call_targets == ()


def test_walker_records_loop_call_targets_not_sinks():
    src = b"def run(repos):\n    for r in repos:\n        helper(r)\n"
    facts = _facts(src)
    targets = dict(facts["run"].loop_call_targets)
    assert "helper" in targets
    assert targets["helper"] == 3  # the call line
    assert facts["run"].bare_sink_kind is None


def test_walker_sink_inside_loop_is_not_a_bare_sink():
    """A sink already nested in a loop is a same-function hit, not a cross-fn
    reachability target — bare_sink_kind stays None for that function."""
    src = (
        b"from sqlalchemy import select\n"
        b"def f(session, repos):\n"
        b"    for r in repos:\n"
        b"        session.execute(select(r))\n"
    )
    facts = _facts(src)
    # ``f`` has a same-function io_in_loop hit, so it is not a bare-sink target.
    assert facts.get("f") is None or facts["f"].bare_sink_kind is None


# ---------------------------------------------------------------------------
# end-to-end bridge over a real resolved calls graph
# ---------------------------------------------------------------------------


def _build(tmp_path: Path, files: dict[str, str]):
    """Write *files*, build the graph, and return ``(walked, graph)``."""
    for name, src in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    trav = FileTraverser(tmp_path)
    parser = ASTParser()
    gb = GraphBuilder(repo_path=tmp_path)
    walked = []
    for fi in trav.traverse():
        data = Path(fi.abs_path).read_bytes()
        parsed = parser.parse_file(fi, data)
        gb.add_file(parsed)
        walked.append((parsed, walk_file(fi.abs_path, fi.language, data)))
    return walked, gb.build()


_SCHEDULER = """
from sqlalchemy import select


class Scheduler:
    def __init__(self, session):
        self.session = session

    def _poll(self, repo):
        return self.session.execute(select(repo))

    def run(self, repos):
        out = []
        for repo in repos:
            out.append(self._poll(repo))
        return out
"""


def test_crossfn_scheduler_pattern_is_caught(tmp_path):
    walked, graph = _build(tmp_path, {"scheduler.py": _SCHEDULER})
    res = collect_crossfn_io_in_loop(walked, graph)
    hits = [h for hs in res.values() for h in hs]
    assert len(hits) == 1
    h = hits[0]
    assert h.kind == "io_in_loop"
    assert h.detail == "db"
    assert h.function == "run"
    # The path is the resolved A -> B chain and ends at the sink holder.
    assert h.path[0].endswith("::run")
    assert h.path[-1].endswith("::_poll")


def test_crossfn_path_renders_in_the_biomarker(tmp_path):
    walked, graph = _build(tmp_path, {"scheduler.py": _SCHEDULER})
    res = collect_crossfn_io_in_loop(walked, graph)
    hit = next(h for hs in res.values() for h in hs)
    ctx = FileContext(
        file_path="scheduler.py",
        language="python",
        nloc=20,
        has_test_file=False,
        module="scheduler",
        perf_hits=[hit],
    )
    (finding,) = IoInLoopDetector().detect(ctx)
    assert finding.biomarker_type == "io_in_loop"
    assert finding.details["cross_function"] is True
    assert finding.details["boundary_kind"] == "db"
    assert finding.details["path"] == list(hit.path)
    assert "cross-function N+1" in finding.reason
    assert "run -> _poll" in finding.reason


def test_crossfn_across_files(tmp_path):
    """The loop and the sink live in different *files* — the case no file-local
    linter can see."""
    files = {
        "db.py": (
            "from sqlalchemy import select\n\n\n"
            "def fetch_one(session, rid):\n"
            "    return session.execute(select(rid))\n"
        ),
        "service.py": (
            "from db import fetch_one\n\n\n"
            "def fetch_all(session, ids):\n"
            "    rows = []\n"
            "    for rid in ids:\n"
            "        rows.append(fetch_one(session, rid))\n"
            "    return rows\n"
        ),
    }
    walked, graph = _build(tmp_path, files)
    res = collect_crossfn_io_in_loop(walked, graph)
    hits = [h for hs in res.values() for h in hs]
    assert len(hits) == 1, "expected one cross-file N+1"
    h = hits[0]
    assert h.function == "fetch_all"
    assert h.path[0].endswith("service.py::fetch_all")
    assert h.path[-1].endswith("db.py::fetch_one")


def test_crossfn_through_star_import_barrel(tmp_path):
    """The persist.py:884 shape: a loop calls a DB wrapper that is imported
    lazily from a package whose ``__init__`` re-exports it via ``from .x import
    *``, and the wrapper's bare name is shadowed by a same-named method (so the
    global-unique tier cannot rescue it). The wildcard re-export must be
    followed for the loop -> wrapper -> sink edge to resolve; without that the
    cross-function N+1 is silently dropped.
    """
    files = {
        "pkg/crud.py": (
            "from sqlalchemy import select\n\n\n"
            "def upsert_one(session, rid):\n"
            "    return session.execute(select(rid))\n"
        ),
        # package barrel re-exports the leaf via a star import
        "pkg/__init__.py": "from pkg.crud import *\n",
        # a same-named method elsewhere defeats the Tier-3 global-unique match,
        # so only the followed barrel edge can resolve the call.
        "stores.py": (
            "class Store:\n"
            "    def upsert_one(self, session, rid):\n"
            "        return None\n"
        ),
        "service.py": (
            "def persist_all(session, ids):\n"
            "    from pkg import upsert_one\n"
            "    for rid in ids:\n"
            "        upsert_one(session, rid)\n"
        ),
    }
    walked, graph = _build(tmp_path, files)
    res = collect_crossfn_io_in_loop(walked, graph)
    hits = [h for hs in res.values() for h in hs]
    assert len(hits) == 1, f"star-barrel N+1 must be caught; got {hits}"
    h = hits[0]
    assert h.function == "persist_all"
    assert h.detail == "db"
    assert h.path[0].endswith("service.py::persist_all")
    assert h.path[-1].endswith("pkg/crud.py::upsert_one")


def test_crossfn_pure_helper_is_not_flagged(tmp_path):
    """A loop calling a helper that does NO I/O produces no cross-fn finding."""
    src = "def square(x):\n    return x * x\n\n\ndef run(xs):\n    return [square(x) for x in xs]\n"
    walked, graph = _build(tmp_path, {"pure.py": src})
    assert collect_crossfn_io_in_loop(walked, graph) == {}


def test_crossfn_same_function_hits_unchanged(tmp_path):
    """The same-function pass still fires exactly as in PR3, and the cross-fn
    pass adds nothing for a directly-nested sink."""
    src = (
        "from sqlalchemy import select\n\n\n"
        "def f(session, repos):\n"
        "    for r in repos:\n"
        "        session.execute(select(r))\n"
    )
    walked, graph = _build(tmp_path, {"direct.py": src})
    (_, fc) = walked[0]
    same_fn = [h for h in fc.perf_hits if h.kind == "io_in_loop" and not h.path]
    assert len(same_fn) == 1
    assert collect_crossfn_io_in_loop(walked, graph) == {}


def test_crossfn_no_graph_is_noop():
    assert collect_crossfn_io_in_loop([], None) == {}


# ---------------------------------------------------------------------------
# guardrail: cross-fn findings score performance only, never defect
# ---------------------------------------------------------------------------


def test_crossfn_finding_scores_performance_not_defect():
    hit = PerfHit("io_in_loop", 14, "run", "db", path=("m.py::run", "m.py::_poll"))
    ctx = FileContext(
        file_path="m.py",
        language="python",
        nloc=10,
        has_test_file=False,
        module="m",
        perf_hits=[hit],
    )
    results = detect_all(ctx)
    perf = [r for r in results if r.biomarker_type == "io_in_loop"]
    assert len(perf) == 1 and perf[0].details["cross_function"] is True
    scores, deductions = score_file(results)
    assert scores["defect"] == 10.0
    assert scores["performance"] < 10.0
    assert all(d == 0.0 for d in deductions)
