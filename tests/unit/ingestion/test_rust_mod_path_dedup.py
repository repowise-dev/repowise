"""Rust ``mod`` items deduped per ``#[path]`` (#2935).

Two ``#[cfg]``-gated ``mod imp;`` declarations that point at different
``#[path]`` files are feature-flag twins, not duplicates: both files are
reachable (one per configuration). The raw-statement dedup used to drop the
second declaration — its text is only ``mod imp;``, because outer attributes
are preceding siblings — so the file it names lost its import edge and was
reported as dead code. The dedup key is now qualified with the ``#[path]``
value, so identical raw text with a different path survives, while a true
duplicate (same raw text, same path — or no path) still dedupes.

The ripgrep shape from the issue::

    #[cfg(not(feature = "unstable-index"))]
    #[path = "disabled.rs"]
    mod imp;
    #[cfg(feature = "unstable-index")]
    #[path = "enabled.rs"]
    mod imp;
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_rust_module_paths import _build, _imports

# ---------------------------------------------------------------------------
# Parser level: which imports survive the dedup
# ---------------------------------------------------------------------------


class TestModPathDedupParsing:
    def test_ripgrep_shape_keeps_both_imports(self) -> None:
        body = (
            '#[cfg(not(feature = "unstable-index"))]\n'
            '#[path = "disabled.rs"]\n'
            "mod imp;\n"
            '#[cfg(feature = "unstable-index")]\n'
            '#[path = "enabled.rs"]\n'
            "mod imp;\n"
        )
        assert [imp[0] for imp in _imports(body)] == ["disabled.rs", "enabled.rs"]

    def test_cfg_platform_twins_keep_both_imports(self) -> None:
        body = (
            '#[cfg(windows)]\n#[path = "win.rs"]\nmod sys;\n'
            '#[cfg(unix)]\n#[path = "unix.rs"]\nmod sys;\n'
        )
        assert [imp[0] for imp in _imports(body)] == ["win.rs", "unix.rs"]

    def test_same_path_twice_still_dedupes(self) -> None:
        body = '#[path = "x.rs"]\nmod a;\n#[path = "x.rs"]\nmod a;\n'
        assert _imports(body) == [("x.rs", ["*"])]

    def test_no_path_same_mod_name_twice_still_dedupes(self) -> None:
        assert _imports("mod a;\nmod a;\n") == [("a", ["*"])]

    def test_different_mod_names_without_paths_both_kept(self) -> None:
        assert _imports("mod a;\nmod b;\n") == [("a", ["*"]), ("b", ["*"])]

    def test_path_and_no_path_same_name_both_kept(self) -> None:
        body = '#[path = "alt.rs"]\nmod a;\nmod a;\n'
        assert [imp[0] for imp in _imports(body)] == ["alt.rs", "a"]

    def test_identical_use_statements_still_dedupes(self) -> None:
        body = "use crate::a::B;\nuse crate::a::B;\n"
        assert _imports(body) == [("crate::a::B", ["B"])]

    def test_cfg_between_declarations_and_path_attribute(self) -> None:
        body = (
            '#[cfg(feature = "f")]\n#[path = "on.rs"]\nmod imp;\n'
            '#[cfg(not(feature = "f"))]\n#[path = "off.rs"]\nmod imp;\n'
        )
        assert [imp[0] for imp in _imports(body)] == ["on.rs", "off.rs"]

    def test_path_attribute_before_cfg_attribute(self) -> None:
        body = (
            '#[path = "first.rs"]\n#[cfg(feature = "f")]\nmod imp;\n'
            '#[path = "second.rs"]\n#[cfg(not(feature = "f"))]\nmod imp;\n'
        )
        assert [imp[0] for imp in _imports(body)] == ["first.rs", "second.rs"]

    def test_doc_comment_above_attribute_is_ignored(self) -> None:
        body = (
            "/// The unstable twin.\n"
            '#[cfg(feature = "u")]\n'
            '#[path = "enabled.rs"]\n'
            "mod imp;\n"
            '#[path = "disabled.rs"]\n'
            "mod imp;\n"
        )
        assert [imp[0] for imp in _imports(body)] == ["enabled.rs", "disabled.rs"]

    def test_path_inside_a_subdirectory(self) -> None:
        body = '#[path = "sub/enabled.rs"]\nmod imp;\n#[path = "sub/disabled.rs"]\nmod imp;\n'
        assert [imp[0] for imp in _imports(body)] == ["sub/enabled.rs", "sub/disabled.rs"]

    def test_pub_mod_twins_keep_both_imports(self) -> None:
        body = '#[path = "a.rs"]\npub mod imp;\n#[path = "b.rs"]\npub mod imp;\n'
        assert [imp[0] for imp in _imports(body)] == ["a.rs", "b.rs"]

    def test_pub_crate_mod_twins_keep_both_imports(self) -> None:
        body = '#[path = "a.rs"]\npub(crate) mod imp;\n#[path = "b.rs"]\npub(crate) mod imp;\n'
        assert [imp[0] for imp in _imports(body)] == ["a.rs", "b.rs"]

    def test_three_way_feature_gate_keeps_all_three(self) -> None:
        body = "".join(
            f'#[cfg(feature = "{name}")]\n#[path = "{name}.rs"]\nmod imp;\n'
            for name in ("stable", "nightly", "dev")
        )
        assert [imp[0] for imp in _imports(body)] == [
            "stable.rs",
            "nightly.rs",
            "dev.rs",
        ]

    def test_paths_differing_only_by_extension_are_distinct(self) -> None:
        body = '#[path = "imp.rs"]\nmod imp;\n#[path = "imp.md"]\nmod imp;\n'
        assert [imp[0] for imp in _imports(body)] == ["imp.rs", "imp.md"]

    def test_plain_single_mod_is_unchanged(self) -> None:
        assert _imports("mod imp;\n") == [("imp", ["*"])]

    def test_mixed_mod_and_use_dedup_independently(self) -> None:
        body = (
            "use crate::x::Y;\n"
            '#[path = "a.rs"]\nmod imp;\n'
            "use crate::x::Y;\n"
            '#[path = "b.rs"]\nmod imp;\n'
        )
        assert [imp[0] for imp in _imports(body)] == [
            "crate::x::Y",
            "a.rs",
            "b.rs",
        ]

    def test_imports_carry_a_star_binding(self) -> None:
        body = '#[path = "enabled.rs"]\nmod imp;\n#[path = "disabled.rs"]\nmod imp;\n'
        assert _imports(body) == [("enabled.rs", ["*"]), ("disabled.rs", ["*"])]

    def test_comment_lines_between_the_declarations(self) -> None:
        body = (
            '#[path = "on.rs"]\nmod imp;\n'
            "// feature-gated alternate below\n"
            '#[path = "off.rs"]\nmod imp;\n'
        )
        assert [imp[0] for imp in _imports(body)] == ["on.rs", "off.rs"]

    def test_two_path_attributes_the_nearest_one_wins(self) -> None:
        body = '#[path = "far.rs"]\n#[path = "near.rs"]\nmod imp;\n'
        assert [imp[0] for imp in _imports(body)] == ["near.rs"]

    def test_empty_file_yields_no_imports(self) -> None:
        assert _imports("") == []


# ---------------------------------------------------------------------------
# End to end: the file named by the second declaration is not dead code
# ---------------------------------------------------------------------------

_RIPGREP_SOURCES = {
    "crates/core/main.rs": "mod index;\n\nfn main() {\n    index::imp::run();\n}\n",
    "crates/core/index/mod.rs": (
        '#[cfg(not(feature = "unstable-index"))]\n'
        '#[path = "disabled.rs"]\n'
        "mod imp;\n"
        '#[cfg(feature = "unstable-index")]\n'
        '#[path = "enabled.rs"]\n'
        "mod imp;\n"
    ),
    "crates/core/index/disabled.rs": "pub fn run() {}\n",
    "crates/core/index/enabled.rs": "pub fn run() {}\n",
}


class TestModPathDedupGraph:
    def test_both_twin_files_have_importer_edges(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, _RIPGREP_SOURCES)
        assert graph.has_edge("crates/core/index/mod.rs", "crates/core/index/disabled.rs")
        assert graph.has_edge("crates/core/index/mod.rs", "crates/core/index/enabled.rs")

    def test_neither_twin_is_reported_as_dead_code(self, tmp_path: Path) -> None:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind

        graph = _build(tmp_path, _RIPGREP_SOURCES)
        report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
            {
                "detect_zombie_packages": False,
                "detect_unused_internals": False,
                "min_confidence": 0.0,
            }
        )
        flagged = {
            f"{f.file_path}::{f.symbol_name}" if f.symbol_name else f.file_path
            for f in report.findings
            if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
        }
        assert "crates/core/index/enabled.rs" not in flagged
        assert "crates/core/index/disabled.rs" not in flagged
        assert "crates/core/index/enabled.rs::run" not in flagged
        assert "crates/core/index/disabled.rs::run" not in flagged

    def test_main_module_edge_is_present(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, _RIPGREP_SOURCES)
        assert graph.has_edge("crates/core/main.rs", "crates/core/index/mod.rs")

    def test_platform_twins_both_reachable(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": "mod sys;\nmod other;\n",
            "src/sys/mod.rs": (
                '#[cfg(windows)]\n#[path = "win.rs"]\nmod imp;\n'
                '#[cfg(unix)]\n#[path = "unix.rs"]\nmod imp;\n'
            ),
            "src/sys/win.rs": "pub fn init() {}\n",
            "src/sys/unix.rs": "pub fn init() {}\n",
            "src/other.rs": "pub fn helper() {}\n",
        }
        graph = _build(tmp_path, sources)
        assert graph.has_edge("src/sys/mod.rs", "src/sys/win.rs")
        assert graph.has_edge("src/sys/mod.rs", "src/sys/unix.rs")

    def test_same_path_twice_does_not_double_the_edge(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": 'mod a;\n#[path = "a.rs"]\nmod a;\n',
            "src/a.rs": "pub fn helper() {}\n",
        }
        graph = _build(tmp_path, sources)
        assert graph.has_edge("src/main.rs", "src/a.rs")

    def test_plain_mod_still_resolves_to_default_layout(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": "mod a;\n",
            "src/a.rs": "pub fn helper() {}\n",
        }
        graph = _build(tmp_path, sources)
        assert graph.has_edge("src/main.rs", "src/a.rs")

    def test_three_way_gate_has_three_edges(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": "mod gate;\n",
            "src/gate/mod.rs": "".join(
                f'#[cfg(feature = "{name}")]\n#[path = "{name}.rs"]\nmod imp;\n'
                for name in ("stable", "nightly", "dev")
            ),
            "src/gate/stable.rs": "pub fn run() {}\n",
            "src/gate/nightly.rs": "pub fn run() {}\n",
            "src/gate/dev.rs": "pub fn run() {}\n",
        }
        graph = _build(tmp_path, sources)
        for name in ("stable", "nightly", "dev"):
            assert graph.has_edge("src/gate/mod.rs", f"src/gate/{name}.rs")

    def test_no_edge_to_an_unrelated_same_named_file(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": 'mod a;\n#[path = "b.rs"]\nmod a;\n',
            "src/a.rs": "pub fn one() {}\n",
            "src/b.rs": "pub fn two() {}\n",
            "src/other.rs": "pub fn three() {}\n",
        }
        graph = _build(tmp_path, sources)
        assert graph.has_edge("src/main.rs", "src/a.rs")
        assert graph.has_edge("src/main.rs", "src/b.rs")
        # The untouched file is not linked to the importer by any of this.
        assert not graph.has_edge("src/main.rs", "src/other.rs")

    def test_nested_path_directory_edges(self, tmp_path: Path) -> None:
        sources = {
            "src/main.rs": "mod imp;\n",
            "src/imp/mod.rs": (
                '#[path = "sub/enabled.rs"]\nmod imp;\n'
                '#[path = "sub/disabled.rs"]\nmod imp;\n'
            ),
            "src/imp/sub/enabled.rs": "pub fn run() {}\n",
            "src/imp/sub/disabled.rs": "pub fn run() {}\n",
        }
        graph = _build(tmp_path, sources)
        assert graph.has_edge("src/imp/mod.rs", "src/imp/sub/enabled.rs")
        assert graph.has_edge("src/imp/mod.rs", "src/imp/sub/disabled.rs")
