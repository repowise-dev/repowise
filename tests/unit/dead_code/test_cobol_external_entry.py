"""COBOL programs are entered by JCL/schedulers outside the source graph."""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer


def _graph(language: str) -> nx.DiGraph:
    graph = nx.DiGraph()
    path = f"jobs/payroll.{language}"
    symbol_id = f"{path}::PAYROLL"
    graph.add_node(
        path,
        node_type="file",
        language=language,
        is_entry_point=False,
        is_test=False,
        symbol_count=1,
    )
    graph.add_node(
        symbol_id,
        node_type="symbol",
        file_path=path,
        language=language,
        name="PAYROLL",
        kind="module",
        visibility="public",
        start_line=1,
        end_line=20,
    )
    graph.add_edge(path, symbol_id, edge_type="defines")
    return graph


def test_cobol_file_and_symbols_are_not_reported_as_dead() -> None:
    report = DeadCodeAnalyzer(_graph("cobol"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert report.findings == []


def test_ordinary_code_language_remains_in_scope() -> None:
    report = DeadCodeAnalyzer(_graph("python"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert {finding.kind for finding in report.findings} == {"unreachable_file"}
