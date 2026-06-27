"""Tests for the Split File refactoring detector (module decomposition).

The detector reads the file's top-level symbols (``defines`` edges) and their
intra-file cohesion (``calls`` edges) off the in-memory graph, builds a
weighted symbol graph, partitions it by modularity, and suggests splitting the
file into the resulting cohesive groups. Fixtures build a tiny NetworkX graph
directly so the symbol set and the call edges — and therefore the expected
partition — are explicit.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
)
from repowise.core.analysis.health.refactoring.split_file import (
    SplitFileDetector,
    _dominant_token,
    _shim_required,
)
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex


def _add_func(g: nx.DiGraph, file_path: str, name: str, *, start: int = 1, end: int = 30) -> str:
    if file_path not in g:
        g.add_node(file_path, node_type="file")
    sid = f"{file_path}::{name}"
    g.add_node(
        sid,
        node_type="symbol",
        kind="function",
        name=name,
        file_path=file_path,
        start_line=start,
        end_line=end,
        parent_name=None,
    )
    g.add_edge(file_path, sid, edge_type="defines")
    return sid


def _add_class(g: nx.DiGraph, file_path: str, name: str, *, start: int = 1, end: int = 30) -> str:
    if file_path not in g:
        g.add_node(file_path, node_type="file")
    sid = f"{file_path}::{name}"
    g.add_node(
        sid,
        node_type="symbol",
        kind="class",
        name=name,
        file_path=file_path,
        start_line=start,
        end_line=end,
        parent_name=None,
    )
    g.add_edge(file_path, sid, edge_type="defines")
    return sid


def _call(g: nx.DiGraph, src: str, dst: str) -> None:
    g.add_edge(src, dst, edge_type="calls")


def _ctx(
    g: nx.DiGraph,
    file_path: str,
    *,
    nloc: int = 600,
    language: str = "python",
    blame_index: BlameIndex | None = None,
    module_map: dict[str, str] | None = None,
) -> RefactoringContext:
    return RefactoringContext(
        file_path=file_path,
        language=language,
        nloc=nloc,
        graph=g,
        blame_index=blame_index,
        module_map=module_map or {},
    )


def _add_foreign_call(g: nx.DiGraph, src: str, foreign_file: str, callee_name: str) -> None:
    """Make *src* (a local symbol id) call a symbol living in *foreign_file*."""
    if foreign_file not in g:
        g.add_node(foreign_file, node_type="file")
    cid = f"{foreign_file}::{callee_name}"
    if cid not in g:
        g.add_node(cid, node_type="symbol", kind="function", name=callee_name, file_path=foreign_file)
    g.add_edge(src, cid, edge_type="calls")


def _imports(g: nx.DiGraph, src_file: str, dst_file: str, names: list[str]) -> None:
    g.add_edge(src_file, dst_file, edge_type="imports", imported_names=list(names))


def _blame_index(ranges: list[tuple[int, int, str]]) -> BlameIndex:
    """Blame index where every line in ``[start, end]`` is attributed to ``sha``."""
    lines: dict[int, tuple[str, int]] = {}
    for start, end, sha in ranges:
        for ln in range(start, end + 1):
            lines[ln] = (sha, 0)
    return BlameIndex(lines=lines, authors={})


def _detect(g: nx.DiGraph, file_path: str, **kw) -> list:
    return [
        s
        for s in detect_refactorings(_ctx(g, file_path, **kw))
        if s.refactoring_type == "split_file"
    ]


def _two_cluster_graph(file_path: str = "big.py", n_each: int = 5) -> nx.DiGraph:
    """Two internally-cohesive clusters with no edges between them — a textbook
    2-way split. Each cluster is a clique of direct calls."""
    g = nx.DiGraph()
    cluster_a = [_add_func(g, file_path, f"alpha_{i}") for i in range(n_each)]
    cluster_b = [_add_func(g, file_path, f"beta_{i}") for i in range(n_each)]
    for cluster in (cluster_a, cluster_b):
        for i in range(len(cluster)):
            for j in range(len(cluster)):
                if i != j:
                    _call(g, cluster[i], cluster[j])
    return g


def test_two_cohesive_clusters_split_into_two_files():
    out = _detect(_two_cluster_graph(), "big.py")
    assert len(out) == 1
    s = out[0]
    assert s.refactoring_type == "split_file"
    assert s.evidence["symbol_count"] == 10
    assert s.evidence["group_count"] == 2
    assert s.evidence["modularity"] >= 0.30
    assert len(s.plan["groups"]) == 2
    # The two clusters partition cleanly: each group is all-alpha or all-beta.
    group_syms = [set(g["symbols"]) for g in s.plan["groups"]]
    for syms in group_syms:
        prefixes = {name.split("_")[0] for name in syms}
        assert len(prefixes) == 1  # no cross-contamination
    assert s.plan["shim_required"] is True
    assert s.impact_delta == 0.0


def test_cohesive_big_file_is_suppressed_by_modularity_gate():
    # One big clique: every symbol calls every other — high cohesion, no clean
    # cut, low modularity → no suggestion (better no split than a bad one).
    g = nx.DiGraph()
    syms = [_add_func(g, "mono.py", f"fn_{i}") for i in range(10)]
    for i in range(len(syms)):
        for j in range(len(syms)):
            if i != j:
                _call(g, syms[i], syms[j])
    assert _detect(g, "mono.py") == []


def test_single_dominant_class_routes_to_extract_class():
    # One class spanning ~90% of the file → Extract Class territory, not Split.
    g = nx.DiGraph()
    _add_class(g, "svc.py", "BigService", start=1, end=540)
    for i in range(8):
        _add_func(g, "svc.py", f"helper_{i}", start=541 + i, end=541 + i)
    assert _detect(g, "svc.py", nloc=600) == []


def test_below_nloc_floor_is_skipped():
    assert _detect(_two_cluster_graph(), "big.py", nloc=100) == []


def test_below_symbol_floor_is_skipped():
    g = nx.DiGraph()
    a = [_add_func(g, "small.py", f"a_{i}") for i in range(3)]
    for i in range(len(a)):
        for j in range(len(a)):
            if i != j:
                _call(g, a[i], a[j])
    assert _detect(g, "small.py") == []


def test_test_file_is_excluded():
    g = _two_cluster_graph("tests/test_big.py")
    assert _detect(g, "tests/test_big.py") == []


def test_generated_and_barrel_files_are_excluded():
    assert _detect(_two_cluster_graph("pkg/__init__.py"), "pkg/__init__.py") == []
    assert _detect(_two_cluster_graph("app/migrations/0001_x.py"), "app/migrations/0001_x.py") == []


def test_no_graph_yields_nothing():
    ctx = RefactoringContext(file_path="big.py", language="python", nloc=600, graph=None)
    assert SplitFileDetector().detect(ctx) == []


def test_deterministic_and_stable_order():
    g = _two_cluster_graph()
    first = _detect(g, "big.py")
    second = _detect(g, "big.py")
    assert [s.target_symbol for s in first] == [s.target_symbol for s in second]
    assert first[0].plan["groups"] == second[0].plan["groups"]


def test_blast_radius_lists_external_referrers():
    g = _two_cluster_graph()
    # An external file calls into one of the file's symbols.
    g.add_node("caller.py", node_type="file")
    g.add_node(
        "caller.py::client",
        node_type="symbol",
        kind="function",
        name="client",
        file_path="caller.py",
        start_line=1,
        end_line=5,
        parent_name=None,
    )
    _call(g, "caller.py::client", "big.py::alpha_0")
    g.add_edge("caller.py", "big.py", edge_type="imports")
    out = _detect(g, "big.py")
    assert len(out) == 1
    br = out[0].blast_radius
    assert "caller.py" in br["dependent_files"]
    assert br["dependent_count"] >= 1
    assert br["import_rewrites"] == br["dependent_count"]  # python → shim language


def test_go_split_needs_no_import_rewrites():
    out = _detect(_two_cluster_graph("pkg/big.go"), "pkg/big.go", language="go")
    assert len(out) == 1
    assert out[0].plan["shim_required"] is False
    assert out[0].blast_radius["import_rewrites"] == 0


def test_shared_helper_groups_callers_together():
    # Two pairs of functions, each pair bound only by a shared local helper
    # (no direct A<->B call), plus a separate cohesive cluster. The shared
    # helper should pull each pair together.
    g = nx.DiGraph()
    h1 = _add_func(g, "f.py", "shared_one")
    a1 = _add_func(g, "f.py", "uses_one_a")
    a2 = _add_func(g, "f.py", "uses_one_b")
    _call(g, a1, h1)
    _call(g, a2, h1)
    h2 = _add_func(g, "f.py", "shared_two")
    b1 = _add_func(g, "f.py", "uses_two_a")
    b2 = _add_func(g, "f.py", "uses_two_b")
    _call(g, b1, h2)
    _call(g, b2, h2)
    # pad to clear the symbol floor with a third cohesive cluster
    c = [_add_func(g, "f.py", f"gamma_{i}") for i in range(2)]
    _call(g, c[0], c[1])
    _call(g, c[1], c[0])
    out = _detect(g, "f.py")
    assert len(out) == 1
    # uses_one_a and uses_one_b end up in the same group.
    groups = [set(grp["symbols"]) for grp in out[0].plan["groups"]]
    assert any({"uses_one_a", "uses_one_b"} <= grp for grp in groups)


def test_dominant_token_helper():
    # Plurality vote: one outlier doesn't kill the shared token.
    assert _dominant_token(["filter_dicts", "filter_path", "filter_rows", "is_excluded"]) == "filter"
    # Stopword verbs and short tokens don't win.
    assert _dominant_token(["get_repo", "get_spec"]) == ""
    # No token shared by >= 2 symbols.
    assert _dominant_token(["alpha", "beta"]) == ""


def test_shim_required_helper():
    assert _shim_required("go") is False
    assert _shim_required("golang") is False
    assert _shim_required("python") is True
    assert _shim_required("typescript") is True
    assert _shim_required(None) is True


def _disconnected_blocks(file_path: str = "big.py") -> nx.DiGraph:
    """Eight top-level functions with NO call edges and distinct 10-line ranges.
    On its own this yields no split (every node is its own community); the
    co-change / import-name signals are what create the partition."""
    g = nx.DiGraph()
    for i in range(8):
        _add_func(g, file_path, f"fn_{i}", start=10 * i + 1, end=10 * i + 10)
    return g


def test_cochange_edge_groups_commit_coupled_symbols():
    # No call edges; the only cohesion is git co-change. Lines of fn_0..3 are
    # all touched by commit "ca", fn_4..7 by "cb" -> two co-change cliques.
    g = _disconnected_blocks()
    blame = _blame_index(
        [(10 * i + 1, 10 * i + 10, "ca" if i < 4 else "cb") for i in range(8)]
    )
    out = _detect(g, "big.py", blame_index=blame)
    assert len(out) == 1
    s = out[0]
    assert s.evidence["group_count"] == 2
    assert s.evidence["cochange_edges"] > 0
    assert "import_edges" not in s.evidence  # no foreign calls in this fixture
    groups = [set(grp["symbols"]) for grp in s.plan["groups"]]
    block_a = {f"fn_{i}" for i in range(4)}
    block_b = {f"fn_{i}" for i in range(4, 8)}
    assert any(grp == block_a for grp in groups)
    assert any(grp == block_b for grp in groups)


def test_cochange_absent_without_blame_index():
    # Same graph, no blame index -> no co-change edges -> nothing to partition.
    assert _detect(_disconnected_blocks(), "big.py") == []


def test_import_name_signal_separates_distinct_dependencies():
    # fn_0..3 lean on ext/a.py, fn_4..7 on ext/b.py. Both foreign files map to
    # the SAME module label, so the old foreign-module proxy would glue all
    # eight together; the imported-name surface (distinct names per file)
    # correctly keeps the two dependency clusters apart.
    g = _disconnected_blocks()
    for i in range(4):
        _add_foreign_call(g, f"big.py::fn_{i}", "ext/a.py", "alpha_dep")
    for i in range(4, 8):
        _add_foreign_call(g, f"big.py::fn_{i}", "ext/b.py", "beta_dep")
    _imports(g, "big.py", "ext/a.py", ["alpha_dep"])
    _imports(g, "big.py", "ext/b.py", ["beta_dep"])
    module_map = {"ext/a.py": "extpkg", "ext/b.py": "extpkg"}
    out = _detect(g, "big.py", module_map=module_map)
    assert len(out) == 1
    s = out[0]
    assert s.evidence["group_count"] == 2
    assert s.evidence["import_edges"] > 0
    groups = [set(grp["symbols"]) for grp in s.plan["groups"]]
    assert any(grp == {f"fn_{i}" for i in range(4)} for grp in groups)
    assert any(grp == {f"fn_{i}" for i in range(4, 8)} for grp in groups)


def test_foreign_module_proxy_is_used_when_imported_names_empty():
    # Lightweight-tier shape: foreign calls but the import edges carry no names.
    # The detector degrades to the foreign-module affinity proxy (today's
    # behavior) -> the two distinct-module clusters still separate.
    g = _disconnected_blocks()
    for i in range(4):
        _add_foreign_call(g, f"big.py::fn_{i}", "ext/a.py", "alpha_dep")
    for i in range(4, 8):
        _add_foreign_call(g, f"big.py::fn_{i}", "ext/b.py", "beta_dep")
    module_map = {"ext/a.py": "moda", "ext/b.py": "modb"}
    out = _detect(g, "big.py", module_map=module_map)
    assert len(out) == 1
    s = out[0]
    assert s.evidence["group_count"] == 2
    assert "import_edges" not in s.evidence  # proxy fired, not the name signal


def test_signals_are_deterministic():
    g = _disconnected_blocks()
    blame = _blame_index(
        [(10 * i + 1, 10 * i + 10, "ca" if i < 4 else "cb") for i in range(8)]
    )
    first = _detect(g, "big.py", blame_index=blame)
    second = _detect(g, "big.py", blame_index=blame)
    assert first[0].plan["groups"] == second[0].plan["groups"]
    assert first[0].evidence == second[0].evidence


def test_empty_blame_index_is_silent():
    # An empty index (the documented "no signal" outcome) must not raise and
    # must not invent co-change edges.
    assert _detect(_disconnected_blocks(), "big.py", blame_index=BlameIndex()) == []
