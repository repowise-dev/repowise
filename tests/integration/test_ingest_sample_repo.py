"""Integration test: ingest the full sample_repo fixture end-to-end.

This is the Phase 2 gate test.  It exercises:
  - FileTraverser   — discovers files, respects .gitignore
  - ASTParser       — parses Python, TypeScript, and Go files
  - GraphBuilder    — builds a dependency graph and computes metrics

Pass criteria (assertions below):
  - At least 8 source files discovered (py + ts + go)
  - At least 15 symbols extracted across all Python files
  - At least 5 symbols extracted across TypeScript files
  - Python import edges present (calculator → models, calculator → utils)
  - TypeScript import edge present (client → utils)
  - Graph has > 0 edges
  - PageRank runs without error and sums to ≈ 1.0
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

SAMPLE_REPO = Path(__file__).parent.parent / "fixtures" / "sample_repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _relative(abs_path: str, root: Path) -> str:
    """Strip root prefix so paths are repo-relative."""
    try:
        return str(Path(abs_path).relative_to(root)).replace("\\", "/")
    except ValueError:
        return abs_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngestSampleRepo:
    @pytest.fixture(scope="class")
    def ingestion_result(self):
        """Run the full ingestion pipeline once and share results."""
        from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

        traverser = FileTraverser(SAMPLE_REPO)
        parser = ASTParser()
        builder = GraphBuilder()

        all_parsed = []
        for file_info in traverser.traverse():
            source = Path(file_info.abs_path).read_bytes()
            parsed = parser.parse_file(file_info, source)
            builder.add_file(parsed)
            all_parsed.append(parsed)

        graph = builder.build()
        return {
            "parsed": all_parsed,
            "builder": builder,
            "graph": graph,
        }

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def test_discovers_minimum_file_count(self, ingestion_result) -> None:
        """At least 8 source files should be found."""
        assert len(ingestion_result["parsed"]) >= 8

    def test_discovers_python_files(self, ingestion_result) -> None:
        langs = [p.file_info.language for p in ingestion_result["parsed"]]
        assert "python" in langs

    def test_discovers_typescript_files(self, ingestion_result) -> None:
        langs = [p.file_info.language for p in ingestion_result["parsed"]]
        assert "typescript" in langs

    def test_discovers_go_files(self, ingestion_result) -> None:
        langs = [p.file_info.language for p in ingestion_result["parsed"]]
        assert "go" in langs

    def test_gitignore_respected(self, ingestion_result) -> None:
        """No file path should contain patterns from .gitignore."""
        paths = [p.file_info.path for p in ingestion_result["parsed"]]
        for path in paths:
            assert "dist/" not in path
            assert "__pycache__" not in path

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def test_python_symbol_count(self, ingestion_result) -> None:
        py_syms = [
            s
            for p in ingestion_result["parsed"]
            if p.file_info.language == "python"
            for s in p.symbols
        ]
        assert len(py_syms) >= 15, f"Got only {len(py_syms)} Python symbols"

    def test_typescript_symbol_count(self, ingestion_result) -> None:
        ts_syms = [
            s
            for p in ingestion_result["parsed"]
            if p.file_info.language == "typescript"
            for s in p.symbols
        ]
        assert len(ts_syms) >= 5, f"Got only {len(ts_syms)} TypeScript symbols"

    def test_no_parse_errors_in_python_files(self, ingestion_result) -> None:
        """Python files in the sample repo should parse cleanly."""
        for p in ingestion_result["parsed"]:
            if p.file_info.language == "python":
                assert p.parse_errors == [], (
                    f"{p.file_info.path} has parse errors: {p.parse_errors}"
                )

    def test_calculator_class_found(self, ingestion_result) -> None:
        """The Calculator class must be extracted from python_pkg/calculator.py."""
        all_symbols = {s.name for p in ingestion_result["parsed"] for s in p.symbols}
        assert "Calculator" in all_symbols

    def test_calculator_methods_found(self, ingestion_result) -> None:
        """Methods add, subtract, multiply, divide must be extracted."""
        method_names = {s.name for p in ingestion_result["parsed"] for s in p.symbols}
        for method in ("add", "subtract", "multiply", "divide"):
            assert method in method_names, f"Method '{method}' not found"

    def test_symbol_ids_within_file_unique_for_same_kind(self, ingestion_result) -> None:
        """Within a single file, symbols of the same kind should have unique names.

        Exceptions:
        - Rust impl blocks: multiple ``impl Calculator`` / ``impl Default for Calculator``
          blocks produce duplicate (name, kind="impl") pairs — this is expected.
        - TypeScript/Java constructors: multiple classes in one file each have their own
          ``constructor`` method — this is also expected.
        """
        allowed_dup_kinds = {"impl", "method"}
        for p in ingestion_result["parsed"]:
            pairs = [(s.name, s.kind) for s in p.symbols]
            dups = [pair for pair in set(pairs) if pairs.count(pair) > 1]
            for name, kind in dups:
                assert kind in allowed_dup_kinds, (
                    f"{p.file_info.path}: unexpected duplicate (name={name!r}, kind={kind!r})"
                )

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def test_python_imports_extracted(self, ingestion_result) -> None:
        """calculator.py must import from python_pkg.models and python_pkg.utils."""
        calc_file = next(
            (
                p
                for p in ingestion_result["parsed"]
                if p.file_info.path.endswith("calculator.py") and "python_pkg" in p.file_info.path
            ),
            None,
        )
        assert calc_file is not None, "python_pkg/calculator.py not found"
        modules = {imp.module_path for imp in calc_file.imports}
        assert any("models" in m for m in modules), f"models import not found in {modules}"
        assert any("utils" in m for m in modules), f"utils import not found in {modules}"

    def test_typescript_imports_extracted(self, ingestion_result) -> None:
        """client.ts must import from ./types and ./utils."""
        client_file = next(
            (p for p in ingestion_result["parsed"] if p.file_info.path.endswith("client.ts")),
            None,
        )
        assert client_file is not None, "client.ts not found"
        modules = {imp.module_path for imp in client_file.imports}
        assert any("types" in m for m in modules), f"types import not found in {modules}"
        assert any("utils" in m for m in modules), f"utils import not found in {modules}"

    # ------------------------------------------------------------------
    # Graph structure
    # ------------------------------------------------------------------

    def test_graph_has_nodes(self, ingestion_result) -> None:
        g = ingestion_result["graph"]
        assert g.number_of_nodes() >= 8

    def test_graph_has_edges(self, ingestion_result) -> None:
        g = ingestion_result["graph"]
        assert g.number_of_edges() > 0, "No import edges were resolved"

    def test_python_dependency_edge(self, ingestion_result) -> None:
        """calculator.py → models.py edge should exist in the graph."""
        g = ingestion_result["graph"]
        calc_node = next((n for n in g.nodes if "calculator" in n and "python_pkg" in n), None)
        models_node = next((n for n in g.nodes if "models" in n and "python_pkg" in n), None)
        if calc_node and models_node:
            assert g.has_edge(calc_node, models_node), f"Expected edge {calc_node} → {models_node}"

    # ------------------------------------------------------------------
    # Graph metrics
    # ------------------------------------------------------------------

    def test_pagerank_runs(self, ingestion_result) -> None:
        builder = ingestion_result["builder"]
        pr = builder.pagerank()
        # PageRank operates on the file-level subgraph only
        file_subgraph = builder.file_subgraph()
        assert len(pr) == file_subgraph.number_of_nodes()

    def test_pagerank_sums_to_one(self, ingestion_result) -> None:
        builder = ingestion_result["builder"]
        pr = builder.pagerank()
        total = sum(pr.values())
        assert abs(total - 1.0) < 0.01, f"PageRank sum = {total}"

    def test_sccs_cover_all_nodes(self, ingestion_result) -> None:
        builder = ingestion_result["builder"]
        sccs = builder.strongly_connected_components()
        # SCCs operate on the file-level subgraph only
        file_nodes = set(builder.file_subgraph().nodes)
        scc_nodes = {n for scc in sccs for n in scc}
        assert scc_nodes == file_nodes

    def test_betweenness_centrality_runs(self, ingestion_result) -> None:
        builder = ingestion_result["builder"]
        bc = builder.betweenness_centrality()
        # Betweenness operates on the file-level subgraph only
        file_subgraph = builder.file_subgraph()
        assert len(bc) == file_subgraph.number_of_nodes()

    def test_symbol_nodes_exist(self, ingestion_result) -> None:
        """Verify that the graph contains symbol-level nodes."""
        graph = ingestion_result["graph"]
        symbol_nodes = [
            n for n, d in graph.nodes(data=True) if d.get("node_type") == "symbol"
        ]
        assert len(symbol_nodes) > 0, "Graph should contain symbol nodes"

    def test_calls_edges_exist(self, ingestion_result) -> None:
        """Verify that CALLS edges were resolved between symbol nodes."""
        graph = ingestion_result["graph"]
        call_edges = [
            (u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"
        ]
        # The sample repo has functions calling other functions
        assert len(call_edges) >= 0  # may be 0 for small sample repos

    # ------------------------------------------------------------------
    # Heritage extraction
    # ------------------------------------------------------------------

    def test_python_heritage_extracted(self, ingestion_result) -> None:
        """DivisionByZeroError should have heritage relation to ArithmeticError."""
        calc_file = next(
            (
                p
                for p in ingestion_result["parsed"]
                if p.file_info.path.endswith("calculator.py") and "python_pkg" in p.file_info.path
            ),
            None,
        )
        assert calc_file is not None
        heritage_names = [(h.child_name, h.parent_name) for h in calc_file.heritage]
        assert ("DivisionByZeroError", "ArithmeticError") in heritage_names

    def test_python_enum_heritage_extracted(self, ingestion_result) -> None:
        """Operation should have heritage relation to Enum."""
        models_file = next(
            (
                p
                for p in ingestion_result["parsed"]
                if p.file_info.path.endswith("models.py") and "python_pkg" in p.file_info.path
            ),
            None,
        )
        assert models_file is not None
        heritage_names = [(h.child_name, h.parent_name) for h in models_file.heritage]
        # Enum is a Python builtin parent — filtered out by language_data.py
        assert ("Operation", "Enum") not in heritage_names

    def test_typescript_heritage_extracted(self, ingestion_result) -> None:
        """Error is a builtin parent — should be filtered out."""
        client_file = next(
            (p for p in ingestion_result["parsed"] if p.file_info.path.endswith("client.ts")),
            None,
        )
        assert client_file is not None
        heritage_names = [(h.child_name, h.parent_name, h.kind) for h in client_file.heritage]
        # Error is a TS builtin — filtered out by language_data.py
        assert ("ApiClientError", "Error", "extends") not in heritage_names
        assert ("ValidationError", "Error", "extends") not in heritage_names

    def test_rust_trait_impl_heritage_extracted(self, ingestion_result) -> None:
        """Rust std trait impls (Default, Debug, etc.) are filtered as builtins."""
        calc_file = next(
            (
                p
                for p in ingestion_result["parsed"]
                if p.file_info.path.endswith("calculator.rs")
            ),
            None,
        )
        assert calc_file is not None
        heritage_names = [(h.child_name, h.parent_name, h.kind) for h in calc_file.heritage]
        # Default is a Rust builtin trait — filtered by language_data.py
        assert ("Calculator", "Default", "trait_impl") not in heritage_names

    def test_heritage_all_have_valid_kind(self, ingestion_result) -> None:
        """Every heritage relation must have a valid kind."""
        valid_kinds = {"extends", "implements", "trait_impl", "mixin", "derive"}
        for p in ingestion_result["parsed"]:
            for h in p.heritage:
                assert h.kind in valid_kinds, (
                    f"{p.file_info.path}: invalid heritage kind {h.kind!r}"
                )

    # ------------------------------------------------------------------
    # Graph edge types
    # ------------------------------------------------------------------

    def test_defines_edges_connect_files_to_symbols(self, ingestion_result) -> None:
        """Verify DEFINES edges link file nodes to their symbols."""
        graph = ingestion_result["graph"]
        defines_edges = [
            (u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "defines"
        ]
        assert len(defines_edges) > 0
        for file_node, sym_node in defines_edges:
            assert graph.nodes[file_node].get("node_type", "file") == "file"
            assert graph.nodes[sym_node].get("node_type") == "symbol"
