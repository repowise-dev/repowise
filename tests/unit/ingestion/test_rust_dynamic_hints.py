"""Tests for the Rust dynamic hints extractor."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.rust import RustDynamicHints


class TestRustDynamicHints:
    def test_detects_tokio_main(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text('#[tokio::main]\nasync fn main() {}')
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:entry_point" for e in edges)

    def test_detects_test_marker(self, tmp_path: Path) -> None:
        (tmp_path / "lib.rs").write_text(
            '#[cfg(test)]\nmod tests {\n  #[test]\n  fn it_works() {}\n}'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:test" for e in edges)

    def test_detects_ffi(self, tmp_path: Path) -> None:
        (tmp_path / "ffi.rs").write_text(
            '#[no_mangle]\npub extern "C" fn hello() {}'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:ffi" for e in edges)

    def test_skips_target_dir(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "target" / "debug"
        target_dir.mkdir(parents=True)
        (target_dir / "main.rs").write_text('#[tokio::main]\nasync fn main() {}')
        edges = RustDynamicHints().extract(tmp_path)
        assert len(edges) == 0

    def test_detects_route_macro(self, tmp_path: Path) -> None:
        (tmp_path / "handler.rs").write_text(
            '#[get("/api/health")]\nfn health() -> &str { "ok" }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:route_macro" for e in edges)

    def test_detects_plugin_registration(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.rs").write_text(
            'inventory::submit! { MyPlugin { name: "test" } }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:plugin" for e in edges)

    def test_detects_wasm_bindgen(self, tmp_path: Path) -> None:
        (tmp_path / "wasm.rs").write_text(
            '#[wasm_bindgen]\npub fn greet(name: &str) -> String { format!("Hello, {}!", name) }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:ffi" for e in edges)

    def test_no_markers_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "plain.rs").write_text('fn add(a: i32, b: i32) -> i32 { a + b }')
        edges = RustDynamicHints().extract(tmp_path)
        assert len(edges) == 0

    def test_detects_proc_macro(self, tmp_path: Path) -> None:
        (tmp_path / "lib.rs").write_text(
            '#[proc_macro]\npub fn my_macro(input: TokenStream) -> TokenStream { input }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:entry_point" for e in edges)

    def test_detects_proc_macro_derive(self, tmp_path: Path) -> None:
        (tmp_path / "lib.rs").write_text(
            '#[proc_macro_derive(MyDerive)]\npub fn derive_my(input: TokenStream) -> TokenStream { input }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:entry_point" for e in edges)

    def test_detects_proc_macro_attribute(self, tmp_path: Path) -> None:
        (tmp_path / "lib.rs").write_text(
            '#[proc_macro_attribute]\npub fn my_attr(attr: TokenStream, item: TokenStream) -> TokenStream { item }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:entry_point" for e in edges)

    def test_detects_rocket_scoped_route(self, tmp_path: Path) -> None:
        (tmp_path / "handler.rs").write_text(
            '#[rocket::get("/health")]\nfn health() -> &str { "ok" }'
        )
        edges = RustDynamicHints().extract(tmp_path)
        assert any(e.hint_source == "rust:route_macro" for e in edges)
