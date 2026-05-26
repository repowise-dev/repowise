"""Dynamic-hint extractor for Rust attribute macros, FFI, and test patterns."""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"target", "node_modules", ".git"}

# Entry point attributes
_ENTRY_ATTRS = (
    "#[tokio::main]",
    "#[actix_web::main]",
    "#[async_std::main]",
    "#[rocket::main]",
    "#[tauri::command]",
)
_ROUTE_RE = re.compile(r"#\[(get|post|put|delete|patch|head|options)\s*\(")

# Test markers
_TEST_MARKERS = ("#[test]", "#[tokio::test]", "#[cfg(test)]", "#[rstest]", "#[bench]")

# FFI / dynamic dispatch markers
_FFI_MARKERS = (
    "#[no_mangle]",
    'extern "C"',
    "#[wasm_bindgen]",
    "#[napi]",
    "#[pyo3::pymethods]",
    "#[pyfunction]",
)

# Plugin registration
_PLUGIN_MARKERS = ("inventory::submit!", "linkme::distributed_slice!")


class RustDynamicHints(DynamicHintExtractor):
    """Discover Rust entry points, route macros, tests, FFI, and plugin registration."""

    name = "rust"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        repo_resolved = repo_root.resolve()

        for src in self._rglob(repo_root, "*.rs"):
            try:
                rel = src.resolve().relative_to(repo_resolved).as_posix()
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in Path(rel).parts):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # Entry point attributes
            for attr in _ENTRY_ATTRS:
                if attr in text:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=rel,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:entry_point",
                        )
                    )
                    break

            # Route macros
            if _ROUTE_RE.search(text):
                edges.append(
                    DynamicEdge(
                        source=rel,
                        target=rel,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:route_macro",
                    )
                )

            # Test markers
            for marker in _TEST_MARKERS:
                if marker in text:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=rel,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:test",
                        )
                    )
                    break

            # FFI exports
            for marker in _FFI_MARKERS:
                if marker in text:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=rel,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:ffi",
                        )
                    )
                    break

            # Plugin registration
            for marker in _PLUGIN_MARKERS:
                if marker in text:
                    edges.append(
                        DynamicEdge(
                            source=rel,
                            target=rel,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:plugin",
                        )
                    )
                    break

        return edges
