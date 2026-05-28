"""Types declared inside a ``namespace JSX`` block are not flagged.

Hono's ``src/jsx/intrinsic-elements.ts`` declares ``namespace JSX``
and exports ~100 interfaces / type aliases (HTMLAttributes,
ButtonHTMLAttributes, CSSProperties, …). They're all referenced
implicitly by every JSX expression via the TypeScript compiler — never
via an ``import`` statement. The exemption is the file-level
``namespace JSX`` source check: any interface / type-alias defined in
a file with that declaration is treated as a JSX transformer
integration point.

Tree-sitter doesn't currently propagate namespace parentage to a
symbol's ``parent_name``, so the source-scan is the working signal.
Symbols outside a ``namespace JSX`` file are still flaggable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import networkx as nx

from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer


def _file_node(graph: nx.DiGraph, path: str, language: str = "typescript") -> None:
    graph.add_node(
        path,
        node_type="file",
        language=language,
        symbol_count=1,
        is_test=False,
        is_entry_point=False,
    )


def _sym_node(
    graph: nx.DiGraph,
    file_path: str,
    name: str,
    kind: str = "interface",
) -> str:
    sid = f"{file_path}::{name}"
    graph.add_node(
        sid,
        node_type="symbol",
        kind=kind,
        name=name,
        file_path=file_path,
        start_line=1,
        end_line=5,
        visibility="public",
        language="typescript",
        decorators=[],
    )
    graph.add_edge(file_path, sid, edge_type="defines")
    return sid


def _fake_parsed(path: str, source: str, tmp_path: Path) -> dict:
    src_file = tmp_path / path
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(source, encoding="utf-8")
    file_info = SimpleNamespace(abs_path=str(src_file), language="typescript", is_test=False)
    pf = SimpleNamespace(file_info=file_info)
    return {path: pf}


class TestJsxNamespaceExemption:
    def test_intrinsic_elements_in_jsx_namespace_not_flagged(self, tmp_path: Path) -> None:
        g = nx.DiGraph()
        # File node has an inbound importer so unused-export pass runs
        # at confidence=1.0 (not "missing implementors" path).
        _file_node(g, "src/jsx/base.ts")
        _file_node(g, "src/importer.ts")
        g.add_edge("src/importer.ts", "src/jsx/base.ts", edge_type="imports", imported_names=[])
        _sym_node(g, "src/jsx/base.ts", "IntrinsicElements", kind="interface")
        _sym_node(g, "src/jsx/base.ts", "ElementChildrenAttribute", kind="interface")
        # Other types declared in the same namespace JSX file — HTML
        # attribute shapes — also implicitly consumed by the transformer.
        _sym_node(g, "src/jsx/base.ts", "HTMLAttributes", kind="interface")
        _sym_node(g, "src/jsx/base.ts", "CSSProperties", kind="type_alias")
        # A function in the same file IS still flaggable (only types
        # get the JSX-namespace exemption).
        _sym_node(g, "src/jsx/base.ts", "someHelper", kind="function")
        parsed = _fake_parsed(
            "src/jsx/base.ts",
            "export namespace JSX { export interface IntrinsicElements {} }\n",
            tmp_path,
        )

        analyzer = DeadCodeAnalyzer(g, parsed_files=parsed)
        report = analyzer.analyze({"min_confidence": 0.0})
        flagged = {f.symbol_name for f in report.findings if f.kind.value == "unused_export"}
        assert "IntrinsicElements" not in flagged
        assert "ElementChildrenAttribute" not in flagged
        assert "HTMLAttributes" not in flagged
        assert "CSSProperties" not in flagged
        # Control: non-type symbols are still flaggable.
        assert "someHelper" in flagged

    def test_whitelist_name_outside_jsx_namespace_still_flagged(self, tmp_path: Path) -> None:
        g = nx.DiGraph()
        _file_node(g, "src/types.ts")
        _file_node(g, "src/importer.ts")
        g.add_edge("src/importer.ts", "src/types.ts", edge_type="imports", imported_names=[])
        _sym_node(g, "src/types.ts", "IntrinsicElements", kind="interface")
        # No ``namespace JSX`` declaration — exemption must NOT apply.
        parsed = _fake_parsed(
            "src/types.ts", "export interface IntrinsicElements {}\n", tmp_path
        )

        analyzer = DeadCodeAnalyzer(g, parsed_files=parsed)
        report = analyzer.analyze({"min_confidence": 0.0})
        flagged = {f.symbol_name for f in report.findings if f.kind.value == "unused_export"}
        assert "IntrinsicElements" in flagged
