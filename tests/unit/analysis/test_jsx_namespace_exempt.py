"""JSX namespace types are not flagged as unused exports.

Hono's ``src/jsx/base.ts`` declares ``namespace JSX`` and exports
``IntrinsicElements``, ``ElementChildrenAttribute`` etc. These names
are referenced implicitly by every JSX expression via the TypeScript
compiler — never via an ``import`` statement. Without an exemption,
they show up as ``unused_export`` interfaces with high confidence.

The exemption is narrow: it requires both (a) the symbol name in the
JSX whitelist AND (b) the defining file containing ``namespace JSX``.
A user-named ``IntrinsicElements`` outside such a file is still
flaggable; a user-named ``UnrelatedType`` inside such a file is too.
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
        # A control symbol — same file, name NOT in the whitelist.
        _sym_node(g, "src/jsx/base.ts", "MyUnrelatedType", kind="interface")
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
        # Control: name not in the whitelist is still flaggable.
        assert "MyUnrelatedType" in flagged

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
