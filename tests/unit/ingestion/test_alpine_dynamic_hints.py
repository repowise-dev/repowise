"""Tests for the Alpine.js dynamic-hint extractor."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.alpine import AlpineDynamicHints


class TestAlpineDynamicHints:
    def test_resolves_data_registration_to_defining_file(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "explorer.js").write_text(
            "export default function explorer() { return {}; }\n"
        )
        (tmp_path / "index.js").write_text(
            "import explorer from './data/explorer.js'\n"
            "Alpine.data('explorer', explorer)\n"
        )
        edges = AlpineDynamicHints().extract(tmp_path)
        assert any(
            e.source == "index.js"
            and e.target == "data/explorer.js"
            and e.edge_type == "dynamic_uses"
            for e in edges
        )

    def test_resolves_store_magic_directive(self, tmp_path: Path) -> None:
        (tmp_path / "search.js").write_text("export const searchStore = {}\n")
        (tmp_path / "clip.js").write_text("function clipboard() {}\n")
        (tmp_path / "tip.js").write_text("const tooltip = () => {}\n")
        (tmp_path / "boot.js").write_text(
            "Alpine.store('search', searchStore)\n"
            "Alpine.magic('clipboard', clipboard)\n"
            "Alpine.directive('tooltip', tooltip)\n"
        )
        targets = {e.target for e in AlpineDynamicHints().extract(tmp_path)}
        assert {"search.js", "clip.js", "tip.js"} <= targets

    def test_inline_handler_emits_no_edge(self, tmp_path: Path) -> None:
        # An inline arrow/object literal has no identifier to resolve.
        (tmp_path / "x.js").write_text("Alpine.data('x', () => ({ open: false }))\n")
        assert AlpineDynamicHints().extract(tmp_path) == []

    def test_unknown_identifier_emits_no_edge(self, tmp_path: Path) -> None:
        # Registration references a symbol defined nowhere in the repo.
        (tmp_path / "x.js").write_text("Alpine.data('x', someExternal)\n")
        assert AlpineDynamicHints().extract(tmp_path) == []

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "thing.js").write_text("function thing() {}\n")
        (nm / "reg.js").write_text("Alpine.data('thing', thing)\n")
        assert AlpineDynamicHints().extract(tmp_path) == []
