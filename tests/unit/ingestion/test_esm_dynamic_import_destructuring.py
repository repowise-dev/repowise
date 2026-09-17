"""Destructured dynamic imports no longer mask unused exports (issue #2230).

The parser used to record ``["*"]`` for every dynamic ``import(...)`` call,
including ``const { fn, calculate } = await import('./mod')``. The wildcard
reads as namespace consumption downstream, so every export in ``./mod``
counted as live and genuinely unused ones were never flagged.
"""

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
        if finding.kind == DeadCodeKind.UNUSED_EXPORT and finding.symbol_name is not None
    }


def test_destructured_dynamic_import_does_not_mask_unused_exports() -> None:
    graph = _graph_from_sources(
        {
            "pkg/app.ts": (
                "export async function load() {\n"
                "  const { Consumed } = await import('./lazy');\n"
                "  return Consumed();\n"
                "}\n"
            ),
            "pkg/lazy.ts": (
                "export function Consumed() { return 1; }\n"
                "export function TrulyUnused() { return 2; }\n"
            ),
        }
    )

    edge = graph["pkg/app.ts"]["pkg/lazy.ts"]
    assert edge["imported_names"] == ["Consumed"]

    unused = _unused_export_names(graph)
    assert "Consumed" not in unused
    assert "TrulyUnused" in unused


def test_bare_dynamic_import_still_keeps_every_export_live() -> None:
    """``const mod = await import('./lazy')`` is namespace consumption.

    The intentional-wildcard side of the same change: a bare declarator holds
    the whole module object, so nothing in the target may be reported unused.
    """
    graph = _graph_from_sources(
        {
            "pkg/app.ts": (
                "export async function load() {\n"
                "  const mod = await import('./lazy');\n"
                "  return mod;\n"
                "}\n"
            ),
            "pkg/lazy.ts": (
                "export function KeptByNamespace() { return 1; }\n"
                "export function AlsoKept() { return 2; }\n"
            ),
        }
    )

    edge = graph["pkg/app.ts"]["pkg/lazy.ts"]
    assert edge["imported_names"] == ["*"]

    unused = _unused_export_names(graph)
    assert "KeptByNamespace" not in unused
    assert "AlsoKept" not in unused
