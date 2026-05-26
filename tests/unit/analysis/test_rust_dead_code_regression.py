"""Regression tests for Rust dead code false positives.

Each test validates that a specific pattern from the Typst audit
does NOT produce a false positive in dead code analysis.
"""
from __future__ import annotations

import pytest
from pathlib import Path


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
