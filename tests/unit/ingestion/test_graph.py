"""Unit tests for GraphBuilder."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fi(path: str, language: str = "python") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parsed(
    path: str,
    language: str = "python",
    imports: list[Import] | None = None,
) -> ParsedFile:
    return ParsedFile(
        file_info=_fi(path, language),
        symbols=[],
        imports=imports or [],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


def _imp(module_path: str, is_relative: bool = False, names: list[str] | None = None) -> Import:
    return Import(
        raw_statement=f"import {module_path}",
        module_path=module_path,
        imported_names=names or [],
        is_relative=is_relative,
        resolved_file=None,
    )


# ---------------------------------------------------------------------------
# Empty graph
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    def test_no_nodes(self) -> None:
        b = GraphBuilder()
        g = b.graph()
        assert g.number_of_nodes() == 0

    def test_pagerank_empty(self) -> None:
        b = GraphBuilder()
        assert b.pagerank() == {}

    def test_betweenness_empty(self) -> None:
        b = GraphBuilder()
        assert b.betweenness_centrality() == {}

    def test_community_empty(self) -> None:
        b = GraphBuilder()
        assert b.community_detection() == {}

    def test_sccs_empty(self) -> None:
        b = GraphBuilder()
        assert b.strongly_connected_components() == []

    def test_to_json_empty(self) -> None:
        b = GraphBuilder()
        data = b.to_json()
        assert "nodes" in data
        assert data["nodes"] == []


# ---------------------------------------------------------------------------
# Node creation
# ---------------------------------------------------------------------------


class TestAddFile:
    def test_node_created(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("src/calc.py"))
        g = b.graph()
        assert "src/calc.py" in g.nodes

    def test_node_attributes(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("src/calc.py"))
        g = b.graph()
        attrs = g.nodes["src/calc.py"]
        assert attrs["language"] == "python"
        assert attrs["symbol_count"] == 0
        assert attrs["has_error"] is False
        assert attrs["is_test"] is False

    def test_multiple_files(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py"))
        g = b.graph()
        assert g.number_of_nodes() == 2


# ---------------------------------------------------------------------------
# Python import resolution
# ---------------------------------------------------------------------------


class TestPythonImports:
    def test_absolute_import_dotted(self) -> None:
        """'from pkg.calc import X' should resolve to pkg/calc.py."""
        b = GraphBuilder()
        b.add_file(_parsed("pkg/calc.py"))
        b.add_file(_parsed("main.py", imports=[_imp("pkg.calc")]))
        b.build()
        assert b.graph().has_edge("main.py", "pkg/calc.py")

    def test_relative_import_sibling(self) -> None:
        """'from . import sibling' resolves to sibling in same directory."""
        b = GraphBuilder()
        b.add_file(_parsed("pkg/calc.py"))
        b.add_file(_parsed("pkg/main.py", imports=[_imp(".calc", is_relative=True)]))
        b.build()
        assert b.graph().has_edge("pkg/main.py", "pkg/calc.py")

    def test_stem_fallback(self) -> None:
        """Stem matching: 'import calculator' → calculator.py anywhere."""
        b = GraphBuilder()
        b.add_file(_parsed("src/calculator.py"))
        b.add_file(_parsed("main.py", imports=[_imp("calculator")]))
        b.build()
        assert b.graph().has_edge("main.py", "src/calculator.py")

    def test_unresolvable_import_no_edge(self) -> None:
        """Unresolvable import produces no edge (no crash)."""
        b = GraphBuilder()
        b.add_file(_parsed("main.py", imports=[_imp("nonexistent_external_lib")]))
        b.build()
        assert b.graph().number_of_edges() == 0

    def test_imported_names_on_edge(self) -> None:
        """Imported names are stored on the edge."""
        b = GraphBuilder()
        b.add_file(_parsed("utils.py"))
        b.add_file(_parsed("main.py", imports=[_imp("utils", names=["helper", "fmt"])]))
        b.build()
        data = b.graph()["main.py"]["utils.py"]
        assert "helper" in data["imported_names"]
        assert "fmt" in data["imported_names"]

    def test_parallel_imports_merged(self) -> None:
        """Two imports of the same module merge their imported_names."""
        b = GraphBuilder()
        b.add_file(_parsed("utils.py"))
        b.add_file(
            _parsed(
                "main.py",
                imports=[
                    _imp("utils", names=["foo"]),
                    _imp("utils", names=["bar"]),
                ],
            )
        )
        b.build()
        names = set(b.graph()["main.py"]["utils.py"]["imported_names"])
        assert "foo" in names
        assert "bar" in names


# ---------------------------------------------------------------------------
# TypeScript import resolution
# ---------------------------------------------------------------------------


class TestTypeScriptImports:
    def test_relative_ts_import(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("src/utils.ts", language="typescript"))
        b.add_file(
            _parsed(
                "src/client.ts",
                language="typescript",
                imports=[_imp("./utils", is_relative=True)],
            )
        )
        b.build()
        assert b.graph().has_edge("src/client.ts", "src/utils.ts")

    def test_external_npm_package(self) -> None:
        b = GraphBuilder()
        b.add_file(
            _parsed(
                "src/app.ts",
                language="typescript",
                imports=[_imp("react")],
            )
        )
        b.build()
        g = b.graph()
        assert any("external:" in n for n in g.nodes)
        external_node = next(n for n in g.nodes if n.startswith("external:"))
        assert g.has_edge("src/app.ts", external_node)


# ---------------------------------------------------------------------------
# Go import resolution
# ---------------------------------------------------------------------------


class TestGoImports:
    def test_go_stem_resolution(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("calculator/calculator.go", language="go"))
        b.add_file(
            _parsed(
                "main.go",
                language="go",
                imports=[_imp("github.com/example/myapp/calculator")],
            )
        )
        b.build()
        assert b.graph().has_edge("main.go", "calculator/calculator.go")


# ---------------------------------------------------------------------------
# Graph idempotency
# ---------------------------------------------------------------------------


class TestBuildIdempotent:
    def test_build_twice_same_edges(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("calc.py"))
        b.add_file(_parsed("main.py", imports=[_imp("calc")]))
        b.build()
        edges1 = list(b.graph().edges())
        b.build()
        edges2 = list(b.graph().edges())
        assert edges1 == edges2

    def test_graph_property_auto_builds(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        g = b.graph()  # should auto-build
        assert "a.py" in g.nodes


# ---------------------------------------------------------------------------
# Graph metrics
# ---------------------------------------------------------------------------


class TestPageRank:
    def test_scores_sum_to_one(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py"))
        b.add_file(_parsed("c.py", imports=[_imp("a"), _imp("b")]))
        b.build()
        pr = b.pagerank()
        assert abs(sum(pr.values()) - 1.0) < 1e-6

    def test_highly_imported_node_higher_rank(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("shared.py"))
        b.add_file(_parsed("a.py", imports=[_imp("shared")]))
        b.add_file(_parsed("c.py", imports=[_imp("shared")]))
        b.add_file(_parsed("d.py", imports=[_imp("shared")]))
        b.add_file(_parsed("isolated.py"))
        b.build()
        pr = b.pagerank()
        assert pr["shared.py"] > pr["isolated.py"]


class TestSCCs:
    def test_acyclic_graph_all_singleton_sccs(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py", imports=[_imp("a")]))
        b.add_file(_parsed("c.py", imports=[_imp("b")]))
        b.build()
        sccs = b.strongly_connected_components()
        # All SCCs of size 1 in a DAG
        assert all(len(s) == 1 for s in sccs)

    def test_cyclic_graph_has_large_scc(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py", imports=[_imp("b")]))
        b.add_file(_parsed("b.py", imports=[_imp("a")]))
        b.build()
        sccs = b.strongly_connected_components()
        large = [s for s in sccs if len(s) > 1]
        assert len(large) == 1
        assert frozenset({"a.py", "b.py"}) in large


class TestBetweenness:
    def test_returns_scores_for_all_nodes(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py"))
        b.add_file(_parsed("c.py"))
        bc = b.betweenness_centrality()
        assert set(bc.keys()) == {"a.py", "b.py", "c.py"}

    def test_bridge_node_higher_centrality(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py", imports=[_imp("bridge")]))
        b.add_file(_parsed("bridge.py", imports=[_imp("z")]))
        b.add_file(_parsed("z.py"))
        b.build()
        bc = b.betweenness_centrality()
        assert bc.get("bridge.py", 0.0) >= bc.get("a.py", 0.0)


class TestCommunityDetection:
    def test_returns_assignment_for_all_nodes(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py"))
        b.add_file(_parsed("c.py"))
        comm = b.community_detection()
        assert set(comm.keys()) == {"a.py", "b.py", "c.py"}
        assert all(isinstance(v, int) for v in comm.values())


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestToJson:
    def test_json_has_expected_keys(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        data = b.to_json()
        assert "nodes" in data
        assert "links" in data or "edges" in data  # networkx version-dependent key name

    def test_json_nodes_match_graph(self) -> None:
        b = GraphBuilder()
        b.add_file(_parsed("x.py"))
        b.add_file(_parsed("y.py"))
        data = b.to_json()
        node_ids = {n["id"] for n in data["nodes"]}
        assert "x.py" in node_ids
        assert "y.py" in node_ids


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersist:
    def test_persist_creates_tables(self, tmp_path: Path) -> None:
        import aiosqlite

        b = GraphBuilder()
        b.add_file(_parsed("a.py"))
        b.add_file(_parsed("b.py", imports=[_imp("a")]))
        b.build()

        db_path = tmp_path / "graph.db"

        async def run() -> None:
            await b.persist(db_path, repo_id="test-repo")
            async with aiosqlite.connect(db_path) as db:
                async with db.execute("SELECT * FROM graph_nodes") as cur:
                    rows = await cur.fetchall()
                assert len(rows) == 2
                async with db.execute("SELECT * FROM graph_edges") as cur:
                    edges = await cur.fetchall()
                assert len(edges) == 1

        asyncio.run(run())

    def test_persist_stores_repo_id(self, tmp_path: Path) -> None:
        import aiosqlite

        b = GraphBuilder()
        b.add_file(_parsed("x.py"))

        db_path = tmp_path / "graph.db"

        async def run() -> None:
            await b.persist(db_path, repo_id="my-project")
            async with aiosqlite.connect(db_path) as db:
                async with db.execute("SELECT repo_id FROM graph_nodes LIMIT 1") as cur:
                    row = await cur.fetchone()
                assert row is not None
                assert row[0] == "my-project"

        asyncio.run(run())
