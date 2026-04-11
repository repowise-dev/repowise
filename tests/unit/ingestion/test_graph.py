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
# Stem disambiguation — protects against the historical PageRank inflation
# bug where a test fixture named like the package (e.g. tests/.../flask.py)
# was the only file with stem "flask" in the stem map (because the real
# src/flask/__init__.py registered under stem "__init__"), so every internal
# `from flask import X` resolved to the test fixture, giving it massive
# in-degree and dominating PageRank.
# ---------------------------------------------------------------------------


class TestStemDisambiguation:
    def test_init_py_registers_under_parent_dir(self) -> None:
        """`from flask import X` resolves to src/flask/__init__.py, not a
        test fixture named flask.py."""
        b = GraphBuilder()
        b.add_file(_parsed("src/flask/__init__.py"))
        b.add_file(_parsed("tests/test_apps/cliapp/inner1/inner2/flask.py"))
        b.add_file(_parsed("src/flask/app.py", imports=[_imp("flask")]))
        b.build()
        g = b.graph()
        assert g.has_edge("src/flask/app.py", "src/flask/__init__.py")
        assert not g.has_edge("src/flask/app.py", "tests/test_apps/cliapp/inner1/inner2/flask.py")

    def test_test_fixture_loses_to_source_file(self) -> None:
        """When two files share a stem and one is under tests/, the
        non-test file wins regardless of insertion order."""
        # Insert test file FIRST so dict iteration would have favored it
        # under the old last-write-wins logic.
        b = GraphBuilder()
        b.add_file(_parsed("tests/fixtures/widget.py"))
        b.add_file(_parsed("src/widget.py"))
        b.add_file(_parsed("main.py", imports=[_imp("widget")]))
        b.build()
        g = b.graph()
        assert g.has_edge("main.py", "src/widget.py")
        assert not g.has_edge("main.py", "tests/fixtures/widget.py")

    def test_resolution_is_deterministic_across_orderings(self) -> None:
        """Two builders with files added in opposite orders must produce
        the same edge — resolution cannot depend on dict iteration."""
        files = ["src/widget.py", "tests/fixtures/widget.py", "examples/widget.py"]

        def build_with_order(order: list[str]) -> str | None:
            b = GraphBuilder()
            for f in order:
                b.add_file(_parsed(f))
            b.add_file(_parsed("main.py", imports=[_imp("widget")]))
            b.build()
            edges = list(b.graph().out_edges("main.py"))
            return edges[0][1] if edges else None

        target_a = build_with_order(files)
        target_b = build_with_order(list(reversed(files)))
        assert target_a == target_b == "src/widget.py"

    def test_parent_dir_match_beats_shorter_path(self) -> None:
        """A nested file whose parent directory matches the stem beats a
        shallower file whose parent doesn't — canonical package layout
        is the strongest signal."""
        b = GraphBuilder()
        # Shallower path, parent dir doesn't match stem
        b.add_file(_parsed("vendor/util.py"))
        # Deeper path, but parent dir == stem (canonical layout)
        b.add_file(_parsed("src/util/util.py"))
        b.add_file(_parsed("main.py", imports=[_imp("util")]))
        b.build()
        assert b.graph().has_edge("main.py", "src/util/util.py")

    def test_src_layout_direct_match(self) -> None:
        """`from flask.app import X` finds src/flask/app.py via the new
        src/ candidate, not via stem fallback."""
        b = GraphBuilder()
        b.add_file(_parsed("src/flask/app.py"))
        # Decoy: another app.py with the same stem in a deep test tree.
        b.add_file(_parsed("tests/test_apps/cliapp/app.py"))
        b.add_file(_parsed("main.py", imports=[_imp("flask.app")]))
        b.build()
        assert b.graph().has_edge("main.py", "src/flask/app.py")

    def test_repo_root_init_does_not_crash(self) -> None:
        """A repo-root __init__.py has no parent directory name; it must
        be skipped from the stem map without crashing the build."""
        b = GraphBuilder()
        b.add_file(_parsed("__init__.py"))
        b.add_file(_parsed("main.py", imports=[_imp("anything")]))
        b.build()  # must not raise
        # No edge expected — stem "anything" is unresolvable
        assert b.graph().number_of_edges() == 0

    def test_go_stem_collision_prefers_parent_match(self) -> None:
        """Go: `import .../calculator` prefers calculator/calculator.go
        over a test fixture with the same filename."""
        b = GraphBuilder()
        b.add_file(_parsed("internal/testdata/calculator.go", language="go"))
        b.add_file(_parsed("calculator/calculator.go", language="go"))
        b.add_file(
            _parsed(
                "main.go",
                language="go",
                imports=[_imp("github.com/example/app/calculator")],
            )
        )
        b.build()
        assert b.graph().has_edge("main.go", "calculator/calculator.go")


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

    def test_tsconfig_alias_resolves_to_file(self, tmp_path: Path) -> None:
        """Non-relative import resolved via TsconfigResolver instead of external:."""
        import json

        from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver

        # Write a tsconfig with @/* -> ./src/*
        tsconfig = tmp_path / "tsconfig.json"
        tsconfig.write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            )
        )

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_parsed("src/utils.ts", language="typescript"))
        b.add_file(
            _parsed(
                "src/app.ts",
                language="typescript",
                imports=[_imp("@/utils")],
            )
        )
        # Attach resolver before build.
        path_set = set(b._parsed_files.keys())
        resolver = TsconfigResolver(repo_path=tmp_path, path_set=path_set)
        b.set_tsconfig_resolver(resolver)
        b.build()

        g = b.graph()
        assert g.has_edge("src/app.ts", "src/utils.ts")
        assert not any(n.startswith("external:@/") for n in g.nodes)

    def test_tsconfig_alias_fallback_to_external(self, tmp_path: Path) -> None:
        """Unmatched alias still creates external: node."""
        import json

        from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver

        tsconfig = tmp_path / "tsconfig.json"
        tsconfig.write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["./src/*"]},
                    }
                }
            )
        )

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(
            _parsed(
                "src/app.ts",
                language="typescript",
                imports=[_imp("react")],
            )
        )
        path_set = set(b._parsed_files.keys())
        resolver = TsconfigResolver(repo_path=tmp_path, path_set=path_set)
        b.set_tsconfig_resolver(resolver)
        b.build()

        g = b.graph()
        assert g.has_edge("src/app.ts", "external:react")

    def test_no_resolver_backwards_compatible(self) -> None:
        """GraphBuilder without resolver behaves identically to before."""
        b = GraphBuilder()
        b.add_file(
            _parsed(
                "src/app.ts",
                language="typescript",
                imports=[_imp("@/utils")],
            )
        )
        b.build()
        g = b.graph()
        # Without resolver, @/utils becomes external:@/utils.
        assert g.has_edge("src/app.ts", "external:@/utils")


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


# ---------------------------------------------------------------------------
# C++ compile_commands.json dependency resolution
# ---------------------------------------------------------------------------


def _cpp(path: str, imports: list[Import] | None = None) -> ParsedFile:
    return _parsed(path, language="cpp", imports=imports or [])


def _cinclude(header: str) -> Import:
    return Import(
        raw_statement=f'#include "{header}"',
        module_path=header,
        imported_names=[],
        is_relative=False,
        resolved_file=None,
    )


class TestCppCompileCommandsResolution:
    def test_include_via_arguments_array(self, tmp_path: Path) -> None:
        """compile_commands 'arguments' array format resolves #include via -I flag."""
        inc_dir = tmp_path / "include"
        inc_dir.mkdir()
        (inc_dir / "foo.hpp").write_text("")  # header exists on disk

        compile_commands = [
            {
                "file": "src/main.cpp",
                "directory": str(tmp_path),
                "arguments": ["g++", "-I", str(inc_dir), "-c", "src/main.cpp"],
            }
        ]
        import json

        (tmp_path / "compile_commands.json").write_text(json.dumps(compile_commands))
        (tmp_path / "src").mkdir()

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("include/foo.hpp"))
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("foo.hpp")]))
        b.build()
        assert b.graph().has_edge("src/main.cpp", "include/foo.hpp")

    def test_include_via_command_string(self, tmp_path: Path) -> None:
        """compile_commands 'command' shell-string format resolves #include via -I flag."""
        inc_dir = tmp_path / "include"
        inc_dir.mkdir()

        compile_commands = [
            {
                "file": "src/main.cpp",
                "directory": str(tmp_path),
                "command": f"g++ -I{inc_dir} -c src/main.cpp",
            }
        ]
        import json

        (tmp_path / "compile_commands.json").write_text(json.dumps(compile_commands))
        (tmp_path / "src").mkdir()

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("include/foo.hpp"))
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("foo.hpp")]))
        b.build()
        assert b.graph().has_edge("src/main.cpp", "include/foo.hpp")

    def test_relative_include_fallback(self, tmp_path: Path) -> None:
        """Without compile_commands.json, #include resolves relative to importer dir."""
        (tmp_path / "src").mkdir()

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("src/utils.hpp"))
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("utils.hpp")]))
        b.build()
        assert b.graph().has_edge("src/main.cpp", "src/utils.hpp")

    def test_stem_fallback_when_no_compile_commands(self, tmp_path: Path) -> None:
        """When compile_commands.json is absent, stem-matching still works for C++."""
        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("lib/crypto.hpp"))
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("crypto.hpp")]))
        b.build()
        assert b.graph().has_edge("src/main.cpp", "lib/crypto.hpp")

    def test_no_compile_commands_no_crash(self, tmp_path: Path) -> None:
        """Missing compile_commands.json does not crash — falls through to stem matching."""
        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("nonexistent.hpp")]))
        b.build()
        # No edge, no exception
        assert b.graph().number_of_edges() == 0

    def test_compile_commands_in_build_subdir(self, tmp_path: Path) -> None:
        """compile_commands.json under build/ subdirectory is also found."""
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        inc_dir = tmp_path / "include"
        inc_dir.mkdir()

        compile_commands = [
            {
                "file": str(tmp_path / "src" / "main.cpp"),
                "directory": str(tmp_path),
                "arguments": ["g++", "-I", str(inc_dir), "-c", "src/main.cpp"],
            }
        ]
        import json

        (build_dir / "compile_commands.json").write_text(json.dumps(compile_commands))
        (tmp_path / "src").mkdir()

        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_cpp("include/bar.hpp"))
        b.add_file(_cpp("src/main.cpp", imports=[_cinclude("bar.hpp")]))
        b.build()
        assert b.graph().has_edge("src/main.cpp", "include/bar.hpp")


# ---------------------------------------------------------------------------
# Rust import resolution
# ---------------------------------------------------------------------------


class TestRustImports:
    def test_crate_import_resolves_to_file(self) -> None:
        """use crate::models::Calculator -> src/models.rs"""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(_parsed("src/models.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/main.rs",
                language="rust",
                imports=[_imp("crate::models::Calculator")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("src/main.rs", "src/models.rs")

    def test_crate_import_resolves_to_mod_rs(self) -> None:
        """use crate::utils -> src/utils/mod.rs when src/utils.rs doesn't exist."""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(_parsed("src/utils/mod.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/main.rs",
                language="rust",
                imports=[_imp("crate::utils")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("src/main.rs", "src/utils/mod.rs")

    def test_super_import(self) -> None:
        """use super::sibling -> resolves to parent dir's sibling.rs."""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(_parsed("src/sibling.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/sub/child.rs",
                language="rust",
                imports=[_imp("super::sibling")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("src/sub/child.rs", "src/sibling.rs")

    def test_self_import(self) -> None:
        """use self::helper -> resolves to current dir's helper.rs."""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(_parsed("src/utils/helper.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/utils/mod.rs",
                language="rust",
                imports=[_imp("self::helper")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("src/utils/mod.rs", "src/utils/helper.rs")

    def test_external_crate(self) -> None:
        """use serde::Deserialize -> external:serde::Deserialize."""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/main.rs",
                language="rust",
                imports=[_imp("serde::Deserialize")],
            )
        )
        b.build()
        g = b.graph()
        assert any(n.startswith("external:") for n in g.nodes)

    def test_nested_crate_import(self) -> None:
        """use crate::api::handlers::auth -> src/api/handlers/auth.rs."""
        b = GraphBuilder()
        b.add_file(_parsed("src/lib.rs", language="rust"))
        b.add_file(_parsed("src/api/handlers/auth.rs", language="rust"))
        b.add_file(
            _parsed(
                "src/main.rs",
                language="rust",
                imports=[_imp("crate::api::handlers::auth")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("src/main.rs", "src/api/handlers/auth.rs")


# ---------------------------------------------------------------------------
# Go import resolution with go.mod
# ---------------------------------------------------------------------------


class TestGoImports:
    def test_go_mod_resolves_local_import(self, tmp_path: Path) -> None:
        """go.mod module path enables resolving internal package imports."""
        (tmp_path / "go.mod").write_text("module github.com/org/myapp\n\ngo 1.21\n")
        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(_parsed("pkg/util/util.go", language="go"))
        b.add_file(
            _parsed(
                "cmd/main.go",
                language="go",
                imports=[_imp("github.com/org/myapp/pkg/util")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("cmd/main.go", "pkg/util/util.go")

    def test_go_external_package(self, tmp_path: Path) -> None:
        """Imports not matching go.mod module path become external: nodes."""
        (tmp_path / "go.mod").write_text("module github.com/org/myapp\n\ngo 1.21\n")
        b = GraphBuilder(repo_path=tmp_path)
        b.add_file(
            _parsed(
                "main.go",
                language="go",
                imports=[_imp("github.com/gin-gonic/gin")],
            )
        )
        b.build()
        g = b.graph()
        assert any(n.startswith("external:") for n in g.nodes)

    def test_go_no_go_mod_falls_back_to_stem(self) -> None:
        """Without go.mod, Go resolution falls back to stem matching."""
        b = GraphBuilder()
        b.add_file(_parsed("calculator/calculator.go", language="go"))
        b.add_file(
            _parsed(
                "main.go",
                language="go",
                imports=[_imp("github.com/example/app/calculator")],
            )
        )
        b.build()
        g = b.graph()
        assert g.has_edge("main.go", "calculator/calculator.go")
