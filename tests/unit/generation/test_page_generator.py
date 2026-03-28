"""Tests for generation/page_generator.py — 25 tests."""

from __future__ import annotations

import hashlib

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig, GeneratedPage
from repowise.core.generation.page_generator import PageGenerator, SYSTEM_PROMPTS
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile, RepoStructure, PackageInfo
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info, _make_symbol

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# SYSTEM_PROMPTS completeness
# ---------------------------------------------------------------------------


EXPECTED_PAGE_TYPES = [
    "file_page", "symbol_spotlight", "module_page", "scc_page",
    "repo_overview", "architecture_diagram", "api_contract", "infra_page",
    "diff_summary",
]


@pytest.mark.parametrize("page_type", EXPECTED_PAGE_TYPES)
def test_system_prompt_exists(page_type):
    assert page_type in SYSTEM_PROMPTS


@pytest.mark.parametrize("page_type", EXPECTED_PAGE_TYPES)
def test_system_prompt_not_empty(page_type):
    assert len(SYSTEM_PROMPTS[page_type]) > 0


# ---------------------------------------------------------------------------
# generate_file_page
# ---------------------------------------------------------------------------


async def test_generate_file_page_returns_generated_page(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    page = await gen.generate_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )

    assert isinstance(page, GeneratedPage)
    assert page.page_type == "file_page"


def test_generate_file_page_provider_name(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    import asyncio
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    page = asyncio.run(
        gen.generate_file_page(
            sample_parsed_file, sample_graph,
            graph_metrics["pagerank"], graph_metrics["betweenness"],
            graph_metrics["community"], sample_source_bytes,
        )
    )
    assert page.provider_name == "mock"
    assert page.model_name == "mock-model-1"


async def test_generate_file_page_increments_call_count(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# Prompt cache
# ---------------------------------------------------------------------------


async def test_cache_hit_does_not_increment_call_count(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    config = GenerationConfig(
        max_tokens=1024, token_budget=2000, max_concurrency=2, cache_enabled=True
    )
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    # Second call — identical inputs → cache hit
    await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    assert provider.call_count == 1


async def test_cache_disabled_increments_every_call(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    config = GenerationConfig(
        max_tokens=1024, token_budget=2000, max_concurrency=2, cache_enabled=False
    )
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    assert provider.call_count == 2


# ---------------------------------------------------------------------------
# Cache key uniqueness
# ---------------------------------------------------------------------------


def test_different_page_type_different_cache_key(sample_config):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    key1 = gen._compute_cache_key("file_page", "same prompt")
    key2 = gen._compute_cache_key("module_page", "same prompt")
    assert key1 != key2


def test_different_prompt_different_cache_key(sample_config):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    key1 = gen._compute_cache_key("file_page", "prompt A")
    key2 = gen._compute_cache_key("file_page", "prompt B")
    assert key1 != key2


# ---------------------------------------------------------------------------
# source_hash and created_at
# ---------------------------------------------------------------------------


async def test_generated_page_source_hash_is_64_hex(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    page = await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    assert len(page.source_hash) == 64
    int(page.source_hash, 16)  # must be valid hex


async def test_generated_page_created_at_is_iso(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    page = await gen.generate_file_page(
        sample_parsed_file, sample_graph,
        graph_metrics["pagerank"], graph_metrics["betweenness"],
        graph_metrics["community"], sample_source_bytes,
    )
    # Must parse without error
    dt = datetime.fromisoformat(page.created_at.replace("Z", "+00:00"))
    assert dt.year >= 2026


# ---------------------------------------------------------------------------
# generate_all — ordering and completeness
# ---------------------------------------------------------------------------


def _make_builder_with(parsed_files):
    """Build a GraphBuilder from a list of ParsedFile objects."""
    from repowise.core.ingestion.graph import GraphBuilder
    builder = GraphBuilder()
    for p in parsed_files:
        builder.add_file(p)
    builder.build()
    return builder


async def test_generate_all_api_contract_before_file_page():
    """api_contract pages (level 0) must appear before file_page pages (level 2)."""
    config = GenerationConfig(max_tokens=256, token_budget=500, max_concurrency=2)
    provider = MockProvider()
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    fi_api = _make_file_info("api/openapi.yaml", language="openapi", is_api_contract=True)
    fi_py = _make_file_info("pkg/main.py", language="python")
    sym = _make_symbol(file_path="pkg/main.py")
    p_api = ParsedFile(
        file_info=fi_api, symbols=[], imports=[], exports=[],
        docstring=None, parse_errors=[]
    )
    p_py = ParsedFile(
        file_info=fi_py, symbols=[sym], imports=[], exports=[],
        docstring=None, parse_errors=[]
    )

    repo = RepoStructure(
        is_monorepo=False,
        packages=[],
        root_language_distribution={"python": 0.5, "openapi": 0.5},
        total_files=2,
        total_loc=50,
        entry_points=[],
    )

    builder = _make_builder_with([p_api, p_py])
    pages = await gen.generate_all(
        [p_api, p_py], {"api/openapi.yaml": b"openapi: 3.0", "pkg/main.py": b"pass"},
        builder, repo, "test-repo"
    )

    api_idx = next((i for i, p in enumerate(pages) if p.page_type == "api_contract"), None)
    file_idx = next((i for i, p in enumerate(pages) if p.page_type == "file_page"), None)
    if api_idx is not None and file_idx is not None:
        assert api_idx < file_idx


async def test_generate_all_infra_file_gets_infra_page():
    """Dockerfile/Makefile should generate infra_page, not file_page."""
    config = GenerationConfig(max_tokens=256, token_budget=500, max_concurrency=2)
    provider = MockProvider()
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    fi_docker = _make_file_info("Dockerfile", language="dockerfile")
    p_docker = ParsedFile(
        file_info=fi_docker, symbols=[], imports=[], exports=[],
        docstring=None, parse_errors=[]
    )
    repo = RepoStructure(
        is_monorepo=False, packages=[],
        root_language_distribution={"dockerfile": 1.0},
        total_files=1, total_loc=10, entry_points=[],
    )
    builder = _make_builder_with([p_docker])
    pages = await gen.generate_all(
        [p_docker], {"Dockerfile": b"FROM ubuntu"}, builder, repo, "test-repo"
    )
    page_types = [p.page_type for p in pages]
    assert "infra_page" in page_types
    assert "file_page" not in page_types


async def test_generate_all_returns_pages():
    """generate_all returns at least 1 page for a non-empty repo."""
    config = GenerationConfig(max_tokens=256, token_budget=500, max_concurrency=2)
    provider = MockProvider()
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    fi = _make_file_info("pkg/main.py", language="python")
    sym = _make_symbol(file_path="pkg/main.py")
    p = ParsedFile(
        file_info=fi, symbols=[sym], imports=[], exports=[],
        docstring=None, parse_errors=[]
    )
    repo = RepoStructure(
        is_monorepo=False, packages=[],
        root_language_distribution={"python": 1.0},
        total_files=1, total_loc=20, entry_points=[],
    )
    builder = _make_builder_with([p])
    pages = await gen.generate_all(
        [p], {"pkg/main.py": b"def main(): pass"}, builder, repo, "test-repo"
    )
    assert len(pages) >= 1


async def test_generate_all_level_values_in_range():
    config = GenerationConfig(max_tokens=256, token_budget=500, max_concurrency=2)
    provider = MockProvider()
    assembler = ContextAssembler(config)
    gen = PageGenerator(provider, assembler, config)

    fi = _make_file_info("pkg/main.py", language="python")
    sym = _make_symbol(file_path="pkg/main.py")
    p = ParsedFile(
        file_info=fi, symbols=[sym], imports=[], exports=[],
        docstring=None, parse_errors=[]
    )
    repo = RepoStructure(
        is_monorepo=False, packages=[],
        root_language_distribution={"python": 1.0},
        total_files=1, total_loc=10, entry_points=[],
    )
    builder = _make_builder_with([p])
    pages = await gen.generate_all(
        [p], {"pkg/main.py": b"def main(): pass"}, builder, repo, "test-repo"
    )
    for page in pages:
        assert 0 <= page.generation_level <= 7
