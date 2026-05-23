"""Tests for tiered doc generation (page_generator/tiering.py + tier-2 path)."""

from __future__ import annotations

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GeneratedPage, GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.tiering import partition_file_tiers
from repowise.core.providers.llm.mock import MockProvider

# ---------------------------------------------------------------------------
# partition_file_tiers — pure partition logic
# ---------------------------------------------------------------------------

_PR = {"a.py": 0.5, "b.py": 0.3, "c.py": 0.1, "d.py": 0.05}
_SELECTED = set(_PR)


def test_partition_none_puts_everything_in_tier1():
    """Default (None) reproduces current selection: all pages tier-1."""
    tier1, tier2 = partition_file_tiers(_SELECTED, _PR, None)
    assert tier1 == _SELECTED
    assert tier2 == set()


def test_partition_n_geq_len_is_all_tier1():
    tier1, tier2 = partition_file_tiers(_SELECTED, _PR, 99)
    assert tier1 == _SELECTED
    assert tier2 == set()


def test_partition_top_n_by_pagerank():
    tier1, tier2 = partition_file_tiers(_SELECTED, _PR, 2)
    # Highest two PageRank scores → tier-1.
    assert tier1 == {"a.py", "b.py"}
    assert tier2 == {"c.py", "d.py"}


def test_partition_zero_is_all_tier2():
    tier1, tier2 = partition_file_tiers(_SELECTED, _PR, 0)
    assert tier1 == set()
    assert tier2 == _SELECTED


def test_partition_is_a_partition():
    tier1, tier2 = partition_file_tiers(_SELECTED, _PR, 3)
    assert tier1 | tier2 == _SELECTED
    assert tier1 & tier2 == set()


def test_partition_tie_break_is_deterministic():
    pr = {"x.py": 0.1, "y.py": 0.1, "z.py": 0.1}
    t1a, _ = partition_file_tiers(set(pr), pr, 1)
    t1b, _ = partition_file_tiers(set(pr), pr, 1)
    assert t1a == t1b  # stable path-based tie-break


# ---------------------------------------------------------------------------
# Tier-2 deterministic render — no LLM call
# ---------------------------------------------------------------------------


async def test_tier2_render_makes_no_provider_call(
    sample_config, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    provider = MockProvider()
    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(provider, assembler, sample_config)

    ctx = assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    page = await gen._generate_file_page_tier2(sample_parsed_file, ctx)

    assert isinstance(page, GeneratedPage)
    assert page.page_type == "file_page"
    assert page.provider_name == "template"
    assert page.metadata["doc_tier"] == 2
    assert page.input_tokens == 0 and page.output_tokens == 0
    # Deterministic structure, no model invocation.
    assert provider.call_count == 0
    assert "## Overview" in page.content
    assert "## Public API" in page.content


def test_generation_config_tier1_top_n_defaults_none():
    assert GenerationConfig().tier1_top_n is None
