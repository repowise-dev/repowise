"""Regression tests for Rust dead code false positives.

Each test validates that a specific pattern from the Typst audit
does NOT produce a false positive in dead code analysis.
"""
from __future__ import annotations

import networkx as nx
import pytest
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind


class TestRustNeverFlagPatterns:
    """Validate never-flag patterns cover Rust conventions."""

    def test_build_rs_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "crates/typst-pdf/build.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_examples_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "examples/hello.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_benches_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "crates/typst/benches/bench.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_tests_dir_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "tests/src/run.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_bin_target_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "crates/typst-cli/src/bin/typst.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_fuzz_target_never_flagged(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        import fnmatch
        path = "tests/fuzz/fuzz_targets/compile.rs"
        assert any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)


class TestRustNeverPackageDirs:
    """Validate test/bench dirs are excluded from zombie-package detection."""

    def test_tests_dir_excluded(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_PACKAGE_DIRS
        assert "tests" in _NEVER_PACKAGE_DIRS

    def test_benches_dir_excluded(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_PACKAGE_DIRS
        assert "benches" in _NEVER_PACKAGE_DIRS

    def test_fuzz_dir_excluded(self):
        from repowise.core.analysis.dead_code.constants import _NEVER_PACKAGE_DIRS
        assert "fuzz" in _NEVER_PACKAGE_DIRS


class TestRustDynamicMarkers:
    """Validate Rust dynamic markers detect proc-macro and doc(hidden) patterns."""

    def test_proc_macro_detected(self):
        from repowise.core.analysis.dead_code.dynamic_markers import _DYNAMIC_IMPORT_MARKERS
        rs_markers = _DYNAMIC_IMPORT_MARKERS.get(".rs", ())
        assert any("proc_macro" in m for m in rs_markers)

    def test_doc_hidden_detected(self):
        from repowise.core.analysis.dead_code.dynamic_markers import _DYNAMIC_IMPORT_MARKERS
        rs_markers = _DYNAMIC_IMPORT_MARKERS.get(".rs", ())
        assert '#[doc(hidden)]' in rs_markers


class TestProcMacroCrateDetection:
    """Validate proc-macro crate detection in workspace indexing."""

    def test_proc_macro_crate_detected(self, tmp_path):
        """A crate with [lib] proc-macro = true should be marked."""
        from repowise.core.ingestion.resolvers.rust_workspace import (
            CargoCrate,
            CargoWorkspaceIndex,
        )

        crate = CargoCrate(
            name="my-macros",
            src_dir="crates/my-macros/src",
            is_proc_macro=True,
        )
        index = CargoWorkspaceIndex(crates=(crate,))
        assert index.is_file_in_proc_macro_crate("crates/my-macros/src/lib.rs")
        assert not index.is_file_in_proc_macro_crate("crates/other/src/lib.rs")

    def test_non_proc_macro_crate_not_detected(self, tmp_path):
        """A regular crate should not be flagged as proc-macro."""
        from repowise.core.ingestion.resolvers.rust_workspace import (
            CargoCrate,
            CargoWorkspaceIndex,
        )

        crate = CargoCrate(
            name="regular-lib",
            src_dir="crates/regular-lib/src",
            is_proc_macro=False,
        )
        index = CargoWorkspaceIndex(crates=(crate,))
        assert not index.is_file_in_proc_macro_crate("crates/regular-lib/src/lib.rs")

    def test_proc_macro_parsed_from_toml(self, tmp_path):
        """Workspace indexing should parse [lib] proc-macro = true from Cargo.toml."""
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/my-macros"]\n'
        )
        crate_dir = tmp_path / "crates" / "my-macros"
        crate_dir.mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(
            '[package]\nname = "my-macros"\nversion = "0.1.0"\n\n'
            '[lib]\nproc-macro = true\n'
        )
        (crate_dir / "src").mkdir()
        (crate_dir / "src" / "lib.rs").write_text("// proc-macro crate\n")

        import networkx as nx
        from repowise.core.ingestion.resolvers.context import ResolverContext
        from repowise.core.ingestion.resolvers.rust_workspace import (
            get_or_build_cargo_workspace_index,
        )

        path_set = {"crates/my-macros/src/lib.rs"}
        stem_map = {"lib": ["crates/my-macros/src/lib.rs"]}
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=nx.DiGraph(),
            repo_path=tmp_path,
        )
        idx = get_or_build_cargo_workspace_index(ctx)
        assert idx is not None
        macro_crate = next((c for c in idx.crates if c.name == "my-macros"), None)
        assert macro_crate is not None
        assert macro_crate.is_proc_macro is True
        assert idx.is_file_in_proc_macro_crate("crates/my-macros/src/lib.rs")


# ---------------------------------------------------------------------------
# Graph builder helper (mirrors tests/unit/test_dead_code.py)
# ---------------------------------------------------------------------------

def _build_graph(
    nodes: dict[str, dict],
    edges: list | None = None,
) -> nx.DiGraph:
    g = nx.DiGraph()
    for name, attrs in nodes.items():
        sym_list = attrs.pop("symbols", [])
        g.add_node(name, **attrs)
        for sym in sym_list:
            sym_id = f"{name}::{sym['name']}"
            g.add_node(sym_id, node_type="symbol", file_path=name, **sym)
            g.add_edge(name, sym_id, edge_type="defines")
    for edge in edges or []:
        if len(edge) == 3:
            g.add_edge(edge[0], edge[1], **(edge[2]))
        else:
            g.add_edge(edge[0], edge[1])
    return g


# ---------------------------------------------------------------------------
# Fix 1: Rust symbols skipped in _detect_unused_internals
# ---------------------------------------------------------------------------

class TestRustUnusedInternalsSkipped:
    """Private Rust symbols must not be flagged by unused_internals.

    The graph builder does not emit intra-file call edges for Rust,
    so every private function would appear 'uncalled'. Fix 1 skips
    all Rust symbols in _detect_unused_internals.
    """

    def test_rust_private_function_not_flagged(self):
        """A private Rust function with no callers should NOT appear."""
        g = _build_graph(
            nodes={
                "crates/my-crate/src/helpers.rs": {
                    "language": "rust",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "do_stuff",
                            "kind": "function",
                            "language": "rust",
                            "visibility": "private",
                            "decorators": [],
                            "start_line": 1,
                            "end_line": 10,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_exports": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
        assert internals == [], f"Rust private function should be skipped, got: {internals}"

    def test_python_private_function_still_flagged(self):
        """Ensure the skip is Rust-only; Python private symbols still flagged."""
        g = _build_graph(
            nodes={
                "pkg/utils.py": {
                    "language": "python",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "_helper",
                            "kind": "function",
                            "language": "python",
                            "visibility": "private",
                            "decorators": [],
                            "start_line": 1,
                            "end_line": 12,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_exports": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
        assert any(f.symbol_name == "_helper" for f in internals)


# ---------------------------------------------------------------------------
# Fix 7: proc_macro-decorated functions exempt from unused_exports
# ---------------------------------------------------------------------------

class TestProcMacroExemptFromUnusedExports:
    """Proc-macro entry points are invoked by the compiler and must not
    be flagged as unused exports.
    """

    def test_proc_macro_function_not_flagged(self):
        """A public function with #[proc_macro] should NOT be flagged."""
        g = _build_graph(
            nodes={
                "crates/my-macros/src/lib.rs": {
                    "language": "rust",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "my_macro",
                            "kind": "function",
                            "language": "rust",
                            "visibility": "public",
                            "decorators": ["proc_macro"],
                            "start_line": 1,
                            "end_line": 20,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        exports = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
        assert all(f.symbol_name != "my_macro" for f in exports), (
            "proc_macro function should be exempt from unused_exports"
        )

    def test_proc_macro_derive_not_flagged(self):
        """A function with #[proc_macro_derive(MyTrait)] should NOT be flagged."""
        g = _build_graph(
            nodes={
                "crates/my-macros/src/lib.rs": {
                    "language": "rust",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "derive_my_trait",
                            "kind": "function",
                            "language": "rust",
                            "visibility": "public",
                            "decorators": ["proc_macro_derive(MyTrait)"],
                            "start_line": 1,
                            "end_line": 30,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        exports = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
        assert all(f.symbol_name != "derive_my_trait" for f in exports)

    def test_proc_macro_attribute_not_flagged(self):
        """A function with #[proc_macro_attribute] should NOT be flagged."""
        g = _build_graph(
            nodes={
                "crates/my-macros/src/lib.rs": {
                    "language": "rust",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "my_attr",
                            "kind": "function",
                            "language": "rust",
                            "visibility": "public",
                            "decorators": ["proc_macro_attribute"],
                            "start_line": 1,
                            "end_line": 15,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        exports = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
        assert all(f.symbol_name != "my_attr" for f in exports)

    def test_regular_rust_public_function_still_flagged(self):
        """A public Rust function without proc_macro decorators IS still flagged."""
        g = _build_graph(
            nodes={
                "crates/my-lib/src/lib.rs": {
                    "language": "rust",
                    "is_entry_point": False,
                    "is_test": False,
                    "is_api_contract": False,
                    "symbol_count": 1,
                    "symbols": [
                        {
                            "name": "unused_pub_fn",
                            "kind": "function",
                            "language": "rust",
                            "visibility": "public",
                            "decorators": [],
                            "start_line": 1,
                            "end_line": 10,
                        },
                    ],
                },
            },
        )
        analyzer = DeadCodeAnalyzer(g, git_meta_map={})
        report = analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        exports = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
        assert any(f.symbol_name == "unused_pub_fn" for f in exports), (
            "Regular public Rust function should still be flagged"
        )
