"""Cohesion edges must not be read as dependency cycles (issue #1294).

Go reports one *package* — a directory of ``.go`` files — as its unit of
compilation. Those files reference each other with no import statement, so
several resolver passes synthesise file-level edges between them to keep
reachability and dead-code analysis honest. Feeding those synthesised edges to
cycle detection turned every cohesive Go package into a "Circular Dependency"
page listing every file in it.

These tests pin the three mechanisms that produced the false cycles and the two
places that consume cycles, and they guard the reachability behaviour the
synthesised edges exist for in the first place.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.health.refactoring.graph_signals import (
    build_file_scc_index,
    cycle_edges,
)
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.cohesion import (
    COHESION_HINTS,
    SAME_PACKAGE_HINT,
    UNIT_FANOUT_LANGUAGES,
    is_cohesion_edge,
)


def _builder(repo: Path) -> GraphBuilder:
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    builder.build()
    return builder


def _cycles(graph: nx.DiGraph) -> list[set[str]]:
    return [set(c) for c in nx.strongly_connected_components(graph) if len(c) > 1]


def _pair_builder(**edge_attrs) -> GraphBuilder:
    """A bare builder holding one mutual a.go <-> b.go pair."""
    import threading

    b = GraphBuilder.__new__(GraphBuilder)
    b._graph = nx.DiGraph()
    b._built = True
    b._subgraph_lock = threading.Lock()
    b._file_subgraph_cache = None
    b._symbol_subgraph_cache = None
    b._cycle_subgraph_cache = None
    b._graph.add_node("a.go", node_type="file")
    b._graph.add_node("b.go", node_type="file")
    b._graph.add_edge("a.go", "b.go", edge_type="imports", **edge_attrs)
    b._graph.add_edge("b.go", "a.go", edge_type="imports", **edge_attrs)
    return b


def _go_pkg(root: Path, module: str = "example.com/app") -> None:
    (root / "go.mod").write_text(f"module {module}\n\ngo 1.22\n")


class TestCohesionPredicate:
    def test_every_synthesising_pass_stops_a_cycle(self) -> None:
        # Behavioural: each hint a resolver pass actually stamps must keep a
        # mutual pair out of cycle detection.
        for hint in COHESION_HINTS:
            b = _pair_builder(hint_source=hint)
            assert _cycles(b.cycle_subgraph()) == [], hint

    def test_real_import_is_not_cohesion(self) -> None:
        assert not is_cohesion_edge({"edge_type": "imports"})
        assert not is_cohesion_edge({"edge_type": "imports", "hint_source": None})

    def test_directional_hints_still_close_a_cycle(self) -> None:
        # An rspec file genuinely depends on its subject; F# compile order is a
        # real dependency. Neither is co-membership, so neither is exempt.
        for hint in ("spec_mirror", "compile_order"):
            b = _pair_builder(hint_source=hint)
            assert _cycles(b.cycle_subgraph()) == [{"a.go", "b.go"}], hint


class TestPythonSiblingsAreRealDependencies:
    def test_python_circular_sibling_import_is_still_a_cycle(self, tmp_path: Path) -> None:
        # Python modules are independent even in one directory: the fan-out
        # exemption must not reach them.
        assert "python" not in UNIT_FANOUT_LANGUAGES
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("from pkg.b import B\n\nclass A: pass\n")
        (pkg / "b.py").write_text("from pkg.a import A\n\nclass B: pass\n")
        b = _builder(tmp_path)
        assert any(
            {"pkg/a.py", "pkg/b.py"} <= c for c in _cycles(b.cycle_subgraph())
        ), "a real Python circular import must still be reported"


class TestCppSameDirectoryIncludesStayDependencies:
    def test_header_impl_pair_is_not_a_cycle(self, tmp_path: Path) -> None:
        (tmp_path / "util.h").write_text("int helper(void);\n")
        (tmp_path / "util.c").write_text('#include "util.h"\nint helper(void){return 1;}\n')
        b = _builder(tmp_path)
        # The pairing pass makes this mutual by construction; it is one unit.
        assert b.file_subgraph().has_edge("util.h", "util.c")
        assert _cycles(b.cycle_subgraph()) == []

    def test_unrelated_same_dir_include_is_still_a_dependency(self, tmp_path: Path) -> None:
        # C's fan-out unit is a build target, not a directory: foo.c -> bar.h in
        # a flat src/ layout is a genuine, cuttable edge and must not be exempt.
        (tmp_path / "bar.h").write_text("int bar(void);\n")
        (tmp_path / "foo.c").write_text('#include "bar.h"\nint foo(void){return bar();}\n')
        graph = _builder(tmp_path).cycle_subgraph()
        assert graph.has_edge("foo.c", "bar.h")


class TestCycleSubgraph:
    def _graph_with(self, **edge_attrs) -> GraphBuilder:
        b = GraphBuilder.__new__(GraphBuilder)
        import threading

        b._graph = nx.DiGraph()
        b._built = True
        b._subgraph_lock = threading.Lock()
        b._file_subgraph_cache = None
        b._symbol_subgraph_cache = None
        b._cycle_subgraph_cache = None
        b._graph.add_node("a.go", node_type="file")
        b._graph.add_node("b.go", node_type="file")
        b._graph.add_edge("a.go", "b.go", edge_type="imports", **edge_attrs)
        b._graph.add_edge("b.go", "a.go", edge_type="imports", **edge_attrs)
        return b

    def test_cohesion_pair_is_not_a_cycle(self) -> None:
        b = self._graph_with(hint_source=SAME_PACKAGE_HINT)
        assert _cycles(b.cycle_subgraph()) == []
        assert [c for c in b.strongly_connected_components() if len(c) > 1] == []

    def test_real_pair_is_still_a_cycle(self) -> None:
        b = self._graph_with()
        assert _cycles(b.cycle_subgraph()) == [{"a.go", "b.go"}]

    def test_file_subgraph_keeps_cohesion_edges(self) -> None:
        # Reachability, PageRank and the degree kernels must be unaffected —
        # dead-code and orphan detection are why these edges are synthesised.
        b = self._graph_with(hint_source=SAME_PACKAGE_HINT)
        fs = b.file_subgraph()
        assert fs.number_of_edges() == 2
        assert fs.in_degree("a.go") == 1
        assert b.cycle_subgraph().number_of_edges() == 0


class TestHealthCycleDefinitionAgrees:
    """break_cycle must not propose cutting an edge the wiki calls no cycle."""

    def _pair(self, **attrs) -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_node("a.go", node_type="file")
        g.add_node("b.go", node_type="file")
        g.add_edge("a.go", "b.go", edge_type="imports", **attrs)
        g.add_edge("b.go", "a.go", edge_type="imports", **attrs)
        return g

    def test_cohesion_pair_yields_no_scc(self) -> None:
        assert build_file_scc_index(self._pair(hint_source=SAME_PACKAGE_HINT)) == {}

    def test_real_pair_yields_an_scc(self) -> None:
        idx = build_file_scc_index(self._pair())
        assert idx["a.go"] == ("a.go", "b.go")

    def test_cohesion_edges_are_not_cut_candidates(self) -> None:
        g = self._pair(hint_source=SAME_PACKAGE_HINT)
        assert cycle_edges(g, ("a.go", "b.go")) == []


class TestGoSamePackageIsNotACycle:
    def test_sibling_files_do_not_form_a_cycle(self, tmp_path: Path) -> None:
        _go_pkg(tmp_path)
        pkg = tmp_path / "acl"
        pkg.mkdir()
        # Mutually referencing siblings — legal Go, one compilation unit.
        (pkg / "acl.go").write_text(
            "package acl\n\ntype ACL struct{ Owner *User }\n"
            "func New(u *User) *ACL { return &ACL{Owner: u} }\n"
        )
        (pkg / "user.go").write_text(
            "package acl\n\ntype User struct{ Rules *ACL }\n"
            "func Owner(a *ACL) *User { return a.Owner }\n"
        )
        b = _builder(tmp_path)
        assert _cycles(b.cycle_subgraph()) == []
        # ...but they are still connected, so neither reads as an orphan.
        fs = b.file_subgraph()
        assert fs.in_degree("acl/user.go") > 0 or fs.in_degree("acl/acl.go") > 0

    def test_importing_own_package_marks_siblings_as_cohesion(self, tmp_path: Path) -> None:
        # An external test package (``package acl_test``) sits in the same
        # directory and imports the package under test; the fan-out lands on
        # every sibling, including the other test files.
        _go_pkg(tmp_path)
        pkg = tmp_path / "acl"
        pkg.mkdir()
        (pkg / "acl.go").write_text("package acl\n\ntype ACL struct{}\n")
        (pkg / "a_test.go").write_text(
            'package acl_test\n\nimport "example.com/app/acl"\n\n'
            "func A() { _ = acl.ACL{} }\n"
        )
        (pkg / "b_test.go").write_text(
            'package acl_test\n\nimport "example.com/app/acl"\n\n'
            "func B() { _ = acl.ACL{} }\n"
        )
        b = _builder(tmp_path)
        assert _cycles(b.cycle_subgraph()) == []
        sibling_edges = [
            (u, v, d)
            for u, v, d in b.file_subgraph().edges(data=True)
            if not v.startswith("external:") and Path(u).parent == Path(v).parent
        ]
        # Guard the loop actually ran — otherwise the assertion below is vacuous.
        assert sibling_edges, "expected the fan-out to produce sibling edges"
        for u, v, d in sibling_edges:
            assert is_cohesion_edge(d), f"{u} -> {v} should be cohesion"

    def test_real_cross_package_cycle_still_reported(self, tmp_path: Path) -> None:
        _go_pkg(tmp_path)
        for name, other in (("alpha", "beta"), ("beta", "alpha")):
            d = tmp_path / name
            d.mkdir()
            (d / f"{name}.go").write_text(
                f"package {name}\n\nimport \"example.com/app/{other}\"\n\n"
                f"func Use() {{ _ = {other}.Name }}\n\nvar Name = \"{name}\"\n"
            )
        b = _builder(tmp_path)
        assert _cycles(b.cycle_subgraph()) == [{"alpha/alpha.go", "beta/beta.go"}]


class TestGoImportResolutionHonesty:
    def test_stdlib_import_does_not_match_a_local_package(self, tmp_path: Path) -> None:
        # hugo's shape: a local ``tpl/strings`` package plus ``import "strings"``.
        # The stdlib import must resolve external, not onto the local package —
        # otherwise it pairs with the local package's real reverse import and
        # fabricates a cycle Go itself would reject.
        _go_pkg(tmp_path)
        tpl = tmp_path / "tpl" / "strings"
        tpl.mkdir(parents=True)
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "general.go").write_text(
            'package helpers\n\nimport "strings"\n\n'
            'func Up(s string) string { return strings.ToUpper(s) }\n'
        )
        (tpl / "strings.go").write_text(
            'package strings\n\nimport "example.com/app/helpers"\n\n'
            "func Up(s string) string { return helpers.Up(s) }\n"
        )
        b = _builder(tmp_path)
        graph = b.graph()
        assert not graph.has_edge("helpers/general.go", "tpl/strings/strings.go")
        assert graph.has_edge("tpl/strings/strings.go", "helpers/general.go")
        assert _cycles(b.cycle_subgraph()) == []

    def test_third_party_import_does_not_match_a_local_package(self, tmp_path: Path) -> None:
        _go_pkg(tmp_path)
        tpl = tmp_path / "tpl" / "transform"
        tpl.mkdir(parents=True)
        (tpl / "transform.go").write_text("package transform\n\ntype T struct{}\n")
        h = tmp_path / "helpers"
        h.mkdir()
        (h / "general.go").write_text(
            'package helpers\n\nimport "golang.org/x/text/transform"\n\n'
            "func F() { _ = transform.Nop }\n"
        )
        graph = _builder(tmp_path).graph()
        assert not graph.has_edge("helpers/general.go", "tpl/transform/transform.go")

    def test_local_package_still_resolves(self, tmp_path: Path) -> None:
        _go_pkg(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (store / "store.go").write_text("package store\n\ntype S struct{}\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "example.com/app/store"\n\n'
            "func main() { _ = store.S{} }\n"
        )
        graph = _builder(tmp_path).graph()
        assert graph.has_edge("main.go", "store/store.go")


class TestExternalTargetsAreNeverCohesion:
    def test_root_file_stdlib_import_is_not_stamped(self, tmp_path: Path) -> None:
        # ``external:strings`` has parent "." — the same value as a root-level
        # file's own directory — so a naive directory test stamps every dot-free
        # stdlib import from the repo root as a same-package sibling.
        _go_pkg(tmp_path)
        (tmp_path / "main.go").write_text(
            'package main\n\nimport (\n\t"strings"\n\t"net/http"\n)\n\n'
            "func main() { _ = strings.ToUpper; _ = http.Get }\n"
        )
        graph = _builder(tmp_path).graph()
        externals = [
            (u, v, d) for u, v, d in graph.edges(data=True) if v.startswith("external:")
        ]
        assert externals, "expected stdlib imports to resolve external"
        for u, v, d in externals:
            assert not is_cohesion_edge(d), f"{u} -> {v} wrongly marked cohesion"


class TestCohesionSurvivesPersistence:
    def test_rehydrated_graph_still_suppresses_the_cycle(self) -> None:
        # hint_source lives only in the NetworkX graph unless it round-trips
        # through graph_edges; the health engine reads a rehydrated graph, so a
        # lost stamp brings every false cycle straight back.
        edges = [
            {
                "source_node_id": "acl/acl.go",
                "target_node_id": "acl/user.go",
                "edge_type": "imports",
                "confidence": 1.0,
                "imported_names": [],
                "hint_source": SAME_PACKAGE_HINT,
            },
            {
                "source_node_id": "acl/user.go",
                "target_node_id": "acl/acl.go",
                "edge_type": "imports",
                "confidence": 1.0,
                "imported_names": [],
                "hint_source": SAME_PACKAGE_HINT,
            },
        ]
        nodes = [
            {"node_id": "acl/acl.go", "node_type": "file"},
            {"node_id": "acl/user.go", "node_type": "file"},
        ]
        b = GraphBuilder.from_persisted(nodes=nodes, edges=edges, metrics={})
        assert _cycles(b.cycle_subgraph()) == []
        assert build_file_scc_index(b.graph()) == {}

    def test_rehydrated_real_cycle_is_still_reported(self) -> None:
        edges = [
            {"source_node_id": "a.py", "target_node_id": "b.py", "edge_type": "imports"},
            {"source_node_id": "b.py", "target_node_id": "a.py", "edge_type": "imports"},
        ]
        nodes = [
            {"node_id": "a.py", "node_type": "file"},
            {"node_id": "b.py", "node_type": "file"},
        ]
        b = GraphBuilder.from_persisted(nodes=nodes, edges=edges, metrics={})
        assert _cycles(b.cycle_subgraph()) == [{"a.py", "b.py"}]


class TestGoLocalReplaceDirectives:
    def test_replace_binds_an_unrelated_import_path_to_a_local_dir(self, tmp_path: Path) -> None:
        # A Go monorepo wires a library in with `replace`; the import path no
        # `module` directive mentions is still local and must keep its edge.
        (tmp_path / "go.mod").write_text(
            "module example.com/app\n\ngo 1.22\n\n"
            "replace github.com/acme/lib => ./lib\n"
        )
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "lib.go").write_text("package lib\n\ntype T struct{}\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "github.com/acme/lib"\n\nfunc main() { _ = lib.T{} }\n'
        )
        graph = _builder(tmp_path).graph()
        assert graph.has_edge("main.go", "lib/lib.go")

    def test_module_to_module_replace_stays_external(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text(
            "module example.com/app\n\ngo 1.22\n\n"
            "replace github.com/acme/lib => github.com/fork/lib v1.2.3\n"
        )
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "lib.go").write_text("package lib\n\ntype T struct{}\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "github.com/acme/lib"\n\nfunc main() {}\n'
        )
        graph = _builder(tmp_path).graph()
        assert not graph.has_edge("main.go", "lib/lib.go")


class TestGoStemFallbackPreservedWithoutModuleMetadata:
    def test_no_go_mod_keeps_the_legacy_stem_match(self, tmp_path: Path) -> None:
        # A pre-modules / GOPATH checkout cannot distinguish ``net/http`` from a
        # GOPATH import root, so the legacy guess stays rather than
        # externalising real local code.
        lib = tmp_path / "util"
        lib.mkdir()
        (lib / "util.go").write_text("package util\n\ntype T struct{}\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "myproject/util"\n\nfunc main() { _ = util.T{} }\n'
        )
        graph = _builder(tmp_path).graph()
        assert graph.has_edge("main.go", "util/util.go")


class TestCycleSubgraphCacheInvalidation:
    def test_adding_a_file_clears_the_cached_cycle_subgraph(self, tmp_path: Path) -> None:
        b = _pair_builder(hint_source=SAME_PACKAGE_HINT)
        assert b.cycle_subgraph().number_of_edges() == 0
        assert b._cycle_subgraph_cache is not None
        b._invalidate_subgraph_caches()
        assert b._cycle_subgraph_cache is None
        b._invalidate_metric_caches()
        assert b._cycle_subgraph_cache is None

    def test_unpickling_an_older_builder_does_not_crash(self) -> None:
        b = _pair_builder(hint_source=SAME_PACKAGE_HINT)
        state = b.__getstate__()
        state.pop("_cycle_subgraph_cache", None)  # an older build's bundle
        revived = GraphBuilder.__new__(GraphBuilder)
        revived.__setstate__(state)
        assert _cycles(revived.cycle_subgraph()) == []


class TestGoImportSurfaceExcludesTestFiles:
    def test_importing_a_package_does_not_reach_its_test_files(self, tmp_path: Path) -> None:
        _go_pkg(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (store / "store.go").write_text("package store\n\ntype S struct{}\n")
        (store / "store_test.go").write_text("package store\n\nfunc TestS(t *T) {}\ntype T struct{}\n")
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "example.com/app/store"\n\n'
            "func main() { _ = store.S{} }\n"
        )
        graph = _builder(tmp_path).graph()
        assert graph.has_edge("main.go", "store/store.go")
        # A test file is never part of the importable package surface.
        assert not graph.has_edge("main.go", "store/store_test.go")

    def test_package_still_owns_its_test_files(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.context import ResolverContext
        from repowise.core.ingestion.resolvers.go_workspace import build_go_package_index

        _go_pkg(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (store / "store.go").write_text("package store\n\ntype S struct{}\n")
        (store / "store_test.go").write_text("package store\n")
        ctx = ResolverContext(
            path_set={"go.mod", "store/store.go", "store/store_test.go"},
            stem_map={},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
            go_modules=(("", "example.com/app"),),
        )
        index = build_go_package_index(ctx)
        pkg = index.package_for_file("store/store_test.go")
        assert pkg is not None
        # Sibling cohesion / dead-code rescue still see the test file.
        assert "store/store_test.go" in pkg.files
        # The import surface does not.
        assert index.files_for_import("example.com/app/store") == ("store/store.go",)
