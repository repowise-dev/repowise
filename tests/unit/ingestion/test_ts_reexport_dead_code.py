"""Regression tests: TS/JS barrel re-exports keep forwarded symbols live.

A symbol reached only through an ``index.ts`` re-export chain (the standard
component-library barrel pattern) used to be flagged as an unused export
because ``export { X } from "./x"`` / ``export * from "./y"`` produced no
graph edge — only ``import ... from`` was captured. These tests drive the
real parser → GraphBuilder → DeadCodeAnalyzer over a barrel chain and assert
the forwarded symbol is no longer flagged.
"""

from __future__ import annotations

from datetime import datetime

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _file_info(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="typescript",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _graph_from_sources(sources: dict[str, str]) -> nx.DiGraph:
    builder = GraphBuilder()
    for path, src in sources.items():
        parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
        builder.add_file(parsed)
    return builder.build()


def _unused_export_names(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


def _unreachable_paths(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})
    return {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}


def _imported_names(src: str) -> list[str]:
    """Parse a single-statement TS source and return the edge's imported names."""
    parsed = _PARSER.parse_file(_file_info("pkg/m.ts"), src.encode("utf-8"))
    assert parsed.imports, f"no import/re-export captured for: {src!r}"
    return parsed.imports[0].imported_names


# ---------------------------------------------------------------------------
# Binding extraction — re-export statements
# ---------------------------------------------------------------------------


class TestReExportBindings:
    def test_named_reexport_records_source_name(self) -> None:
        assert _imported_names('export { SearchBar } from "./SearchBar";') == ["SearchBar"]

    def test_aliased_reexport_records_source_name(self) -> None:
        # `A as B` re-exports the source module's `A`; reachability is about A.
        assert _imported_names('export { A as B } from "./m";') == ["A"]

    def test_wildcard_reexport_is_star(self) -> None:
        assert _imported_names('export * from "./panels";') == ["*"]

    def test_namespace_reexport_is_star(self) -> None:
        assert _imported_names('export * as ns from "./m";') == ["*"]

    def test_local_export_is_not_an_import(self) -> None:
        # `export const x` has no `source` — it must not be captured as an import.
        parsed = _PARSER.parse_file(_file_info("pkg/m.ts"), b"export const x = 1;\n")
        assert parsed.imports == []


# ---------------------------------------------------------------------------
# End-to-end — barrel chain keeps the leaf symbol live
# ---------------------------------------------------------------------------


def test_symbol_reexported_through_barrel_chain_not_flagged() -> None:
    sources = {
        "pkg/src/comp/search-bar.tsx": "export function SearchBar() {\n  return null;\n}\n",
        "pkg/src/comp/index.ts": 'export { SearchBar } from "./search-bar";\n',
        "pkg/src/index.ts": 'export * from "./comp";\n',
        "pkg/src/app.tsx": (
            'import { SearchBar } from "./index";\n\n'
            "export function App() {\n  return SearchBar();\n}\n"
        ),
    }
    graph = _graph_from_sources(sources)
    assert "SearchBar" not in _unused_export_names(graph)
    # Every barrel + leaf is reachable through the re-export edges.
    assert "pkg/src/comp/search-bar.tsx" not in _unreachable_paths(graph)


def test_barrel_files_never_flagged_unreachable() -> None:
    """An `index.ts` barrel with no inbound import is still not flagged."""
    sources = {
        "pkg/src/widget.tsx": "export function Widget() {\n  return null;\n}\n",
        "pkg/src/index.ts": 'export { Widget } from "./widget";\n',
    }
    graph = _graph_from_sources(sources)
    assert "pkg/src/index.ts" not in _unreachable_paths(graph)
