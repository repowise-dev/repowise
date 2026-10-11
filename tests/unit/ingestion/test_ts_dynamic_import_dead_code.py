"""Literal TS/JS dynamic imports consume the target module namespace."""

from __future__ import annotations

from datetime import datetime

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


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
    parser = ASTParser()
    builder = GraphBuilder()
    for path, source in sources.items():
        builder.add_file(parser.parse_file(_file_info(path), source.encode("utf-8")))
    return builder.build()


def _unused_export_names(graph: nx.DiGraph) -> set[str]:
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False}
    )
    return {
        finding.symbol_name
        for finding in report.findings
        if finding.kind == DeadCodeKind.UNUSED_EXPORT
    }


def test_literal_dynamic_import_keeps_target_exports_live() -> None:
    graph = _graph_from_sources(
        {
            "pkg/app.ts": "export async function load() { return import('./lazy'); }\n",
            "pkg/lazy.ts": "export function UsedByDynamicImport() { return 1; }\n",
            "pkg/unrelated.ts": "export function TrulyUnused() { return 2; }\n",
        }
    )

    edge = graph["pkg/app.ts"]["pkg/lazy.ts"]
    assert edge["edge_type"] == "imports"
    assert edge["imported_names"] == ["*"]

    unused = _unused_export_names(graph)
    assert "UsedByDynamicImport" not in unused
    assert "TrulyUnused" in unused
