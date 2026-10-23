"""COBOL programs are entered by JCL/schedulers outside the source graph."""

from __future__ import annotations

import networkx as nx
import pytest

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


def _private_method_graph(language: str) -> nx.DiGraph:
    graph = nx.DiGraph()
    path = f"src/Payroll.{language}"
    graph.add_node(path, node_type="file", language=language, is_entry_point=True,
                   is_test=False, symbol_count=1)
    graph.add_node(f"{path}::Helper", node_type="symbol", file_path=path, language=language,
                   name="Helper", kind="class", visibility="private", start_line=5, end_line=8)
    graph.add_edge(path, f"{path}::Helper", edge_type="defines")
    return graph


@pytest.mark.parametrize("language", ["apex", "cobol"])
def test_exempt_language_private_symbol_is_not_an_unused_internal(language: str) -> None:
    # Apex private symbols back triggers, @AuraEnabled and test classes the
    # graph cannot see yet, so the internal pass honours the exemption too.
    report = DeadCodeAnalyzer(_private_method_graph(language), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert report.findings == []
    control = DeadCodeAnalyzer(_private_method_graph("java"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert "unused_internal" in {finding.kind for finding in control.findings}
