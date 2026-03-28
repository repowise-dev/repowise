"""Tests for generation/context_assembler.py — 22 tests."""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.context_assembler import (
    ContextAssembler,
    FilePageContext,
    SccPageContext,
)
from repowise.core.generation.models import GenerationConfig
from repowise.core.ingestion.models import (
    ParsedFile,
)

from .conftest import _make_file_info, _make_symbol

# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_string(sample_config):
    assembler = ContextAssembler(sample_config)
    assert assembler._estimate_tokens("") == 0


def test_estimate_tokens_known_string(sample_config):
    assembler = ContextAssembler(sample_config)
    text = "a" * 400
    assert assembler._estimate_tokens(text) == 100


def test_estimate_tokens_short_string(sample_config):
    assembler = ContextAssembler(sample_config)
    assert assembler._estimate_tokens("abcd") == 1


# ---------------------------------------------------------------------------
# _trim_to_budget
# ---------------------------------------------------------------------------


def test_trim_to_budget_long_text_truncated(sample_config):
    assembler = ContextAssembler(sample_config)
    long_text = "x" * 10000
    result = assembler._trim_to_budget(long_text, 10)
    assert result.endswith("...[truncated]")
    assert len(result) <= 10 * 4 + len("...[truncated]")


def test_trim_to_budget_short_text_unchanged(sample_config):
    assembler = ContextAssembler(sample_config)
    short_text = "hello world"
    result = assembler._trim_to_budget(short_text, 100)
    assert result == short_text


def test_trim_to_budget_zero_remaining(sample_config):
    assembler = ContextAssembler(sample_config)
    result = assembler._trim_to_budget("some text", 0)
    assert result == ""


# ---------------------------------------------------------------------------
# assemble_file_page
# ---------------------------------------------------------------------------


def test_assemble_file_page_returns_context(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    assert isinstance(ctx, FilePageContext)
    assert ctx.file_path == sample_parsed_file.file_info.path
    assert ctx.language == "python"


def test_assemble_file_page_dependents_from_graph(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """dependents = files that have in-edges to this file (files importing it)."""
    assembler = ContextAssembler(sample_config)
    # Add a node that imports calculator
    path = sample_parsed_file.file_info.path
    graph = sample_graph.copy()
    graph.add_node("another.py")
    graph.add_edge("another.py", path)
    ctx = assembler.assemble_file_page(
        sample_parsed_file,
        graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    assert "another.py" in ctx.dependents


def test_assemble_file_page_dependencies_from_graph(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """dependencies = files this file imports (out-edges)."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    assert "python_pkg/models.py" in ctx.dependencies
    assert "python_pkg/utils.py" in ctx.dependencies


def test_assemble_file_page_token_budget_respected(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    assert ctx.estimated_tokens <= sample_config.token_budget


def test_assemble_file_page_private_undocumented_dropped_first():
    """Private undocumented symbols are dropped first when over budget."""
    tiny_config = GenerationConfig(max_tokens=256, token_budget=5)
    assembler = ContextAssembler(tiny_config)

    fi = _make_file_info()
    public_sym = _make_symbol(name="public_func", visibility="public", signature="a" * 100)
    private_undoc = _make_symbol(
        name="_private", visibility="private", docstring=None, signature="b" * 100
    )
    parsed = ParsedFile(
        file_info=fi,
        symbols=[public_sym, private_undoc],
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=[],
    )
    g = nx.DiGraph()
    g.add_node(fi.path)
    ctx = assembler.assemble_file_page(parsed, g, {}, {}, {}, b"")
    symbol_names = [s["name"] for s in ctx.symbols]
    # Private undocumented should not appear (budget too small)
    assert "_private" not in symbol_names


# ---------------------------------------------------------------------------
# assemble_symbol_spotlight
# ---------------------------------------------------------------------------


def test_assemble_symbol_spotlight_callers(
    sample_config, sample_parsed_file, sample_graph, graph_metrics
):
    assembler = ContextAssembler(sample_config)
    symbol = sample_parsed_file.symbols[0]  # Calculator class
    # Add caller
    graph = sample_graph.copy()
    graph.add_edge("caller.py", sample_parsed_file.file_info.path)
    ctx = assembler.assemble_symbol_spotlight(
        symbol, sample_parsed_file, graph_metrics["pagerank"], graph
    )
    assert "caller.py" in ctx.callers


def test_assemble_symbol_spotlight_no_callers(
    sample_config, sample_parsed_file, sample_graph, graph_metrics
):
    assembler = ContextAssembler(sample_config)
    symbol = sample_parsed_file.symbols[0]
    ctx = assembler.assemble_symbol_spotlight(
        symbol, sample_parsed_file, graph_metrics["pagerank"], sample_graph
    )
    # calculator.py is not imported by any file in sample_graph
    assert isinstance(ctx.callers, list)


# ---------------------------------------------------------------------------
# assemble_module_page
# ---------------------------------------------------------------------------


def test_assemble_module_page_total_symbols(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    assembler = ContextAssembler(sample_config)
    fc = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    ctx = assembler.assemble_module_page("python_pkg", "python", [fc], sample_graph)
    assert ctx.total_symbols == len(fc.symbols)


def test_assemble_module_page_public_symbols(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    assembler = ContextAssembler(sample_config)
    fc = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    ctx = assembler.assemble_module_page("python_pkg", "python", [fc], sample_graph)
    assert ctx.public_symbols >= 0


# ---------------------------------------------------------------------------
# assemble_scc_page
# ---------------------------------------------------------------------------


def test_assemble_scc_page_cycle_description_contains_files(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    assembler = ContextAssembler(sample_config)
    fc = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    scc_files = [sample_parsed_file.file_info.path, "python_pkg/models.py"]
    ctx = assembler.assemble_scc_page("scc-0", scc_files, [fc])
    assert isinstance(ctx, SccPageContext)
    for f in scc_files:
        assert f in ctx.cycle_description


# ---------------------------------------------------------------------------
# assemble_repo_overview
# ---------------------------------------------------------------------------


def test_assemble_repo_overview_top_files_sorted(
    sample_config, sample_graph, sample_repo_structure
):
    assembler = ContextAssembler(sample_config)
    pagerank = {"python_pkg/calculator.py": 0.5, "python_pkg/models.py": 0.3}
    ctx = assembler.assemble_repo_overview(
        sample_repo_structure, pagerank, [], {n: 0 for n in sample_graph.nodes()}
    )
    if len(ctx.top_files_by_pagerank) >= 2:
        assert ctx.top_files_by_pagerank[0].score >= ctx.top_files_by_pagerank[1].score


def test_assemble_repo_overview_circular_dep_count(
    sample_config, sample_graph, sample_repo_structure
):
    assembler = ContextAssembler(sample_config)
    # Provide one true SCC (len > 1) and one singleton
    sccs = [frozenset(["a.py", "b.py"]), frozenset(["c.py"])]
    ctx = assembler.assemble_repo_overview(sample_repo_structure, {}, sccs, {})
    assert ctx.circular_dependency_count == 1


# ---------------------------------------------------------------------------
# assemble_api_contract
# ---------------------------------------------------------------------------


def test_assemble_api_contract_raw_content_budgeted(sample_config, sample_parsed_file):
    assembler = ContextAssembler(sample_config)
    huge_bytes = b"x" * 100_000
    ctx = assembler.assemble_api_contract(sample_parsed_file, huge_bytes)
    assert assembler._estimate_tokens(ctx.raw_content) <= sample_config.token_budget


def test_assemble_api_contract_language_matches(sample_config, sample_parsed_file):
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_api_contract(sample_parsed_file, b"content")
    assert ctx.language == sample_parsed_file.file_info.language
