"""A page says how far it can be trusted, and the three answers are different.

``confidence`` stood at a constant 1.0 on every page a run produced. A constant
column cannot gate anything: retrieval cannot weight by it, the reader's
low-confidence banner had never once rendered, and — the case that matters — a
wiki where a provider outage left hundreds of stubs looked exactly as
trustworthy as a complete one.

Three values, because three distinctions are actually available when a page is
written:

* a template page is everything the parse, the graph and git history know, with
  no model in the loop;
* a model page is grounded in that material and checked against it, but it is a
  summary of the code rather than an extraction from it;
* a stub is that same material with the prose missing, standing in for a page a
  model was meant to write. That is the one a reader has to be warned about, so
  it is the one that sits below the banner threshold.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import (
    MODEL_PAGE_CONFIDENCE,
    STUB_FALLBACK_ERROR,
    STUB_PAGE_CONFIDENCE,
    TEMPLATE_PAGE_CONFIDENCE,
    GenerationConfig,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.mock import MockProvider

# The reader UI shows its warning below this. Duplicated from the component on
# purpose: the point of the values above is where they sit relative to it, and
# a test that imported the threshold would move with it and stop asserting the
# relationship.
BANNER_THRESHOLD = 0.5


class _OutageProvider(MockProvider):
    """Fails the way a real provider outage does: every call raises."""

    async def generate(self, *args, **kwargs):
        raise RuntimeError("upstream 529 overloaded")


@pytest.fixture
def config() -> GenerationConfig:
    return GenerationConfig(max_tokens=1024, token_budget=2000, max_concurrency=2)


@pytest.fixture
def generator(config) -> PageGenerator:
    return PageGenerator(MockProvider(), ContextAssembler(config), config)


@pytest.fixture
def outage_generator(config) -> PageGenerator:
    return PageGenerator(_OutageProvider(), ContextAssembler(config), config)


async def test_a_template_page_is_fully_confident(
    generator, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Nothing on it came from a model, so there is nothing to be unsure about."""
    page = await generator.generate_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )

    assert page.confidence == TEMPLATE_PAGE_CONFIDENCE
    assert page.confidence > BANNER_THRESHOLD


async def test_a_model_written_page_is_confident_but_not_certain(generator):
    page = await generator.generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.confidence == MODEL_PAGE_CONFIDENCE
    assert page.confidence < TEMPLATE_PAGE_CONFIDENCE
    # A model page is normal, not suspect: it must not raise the reader's flag.
    assert page.confidence > BANNER_THRESHOLD


async def test_a_page_lost_to_a_provider_outage_says_so(outage_generator):
    """The failure this exists to make visible.

    The stub is real material with the prose missing. Before this it claimed
    the same confidence as a page that was actually written, so a wiki with
    hundreds of them read as complete.
    """
    page = await outage_generator.generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.metadata[STUB_FALLBACK_ERROR]
    assert page.confidence == STUB_PAGE_CONFIDENCE
    assert page.confidence < BANNER_THRESHOLD


async def test_a_keyless_stub_is_marked_the_same_way(config):
    """No provider is configured at all, so nothing wrote the prose either."""
    deterministic = GenerationConfig(
        max_tokens=1024, token_budget=2000, max_concurrency=2, deterministic=True
    )
    generator = PageGenerator(MockProvider(), ContextAssembler(deterministic), deterministic)

    page = await generator.generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.confidence == STUB_PAGE_CONFIDENCE
    assert page.confidence < BANNER_THRESHOLD


async def test_the_run_produces_more_than_one_value(
    generator,
    outage_generator,
    sample_parsed_file,
    sample_graph,
    graph_metrics,
    sample_source_bytes,
):
    """The whole point: a column with a distribution, not a constant.

    Asserted as a set rather than page by page, because the failure being
    prevented is every page agreeing — which each individual assertion above
    would still pass under, if they all agreed on the wrong value.
    """
    pages = [
        await generator.generate_file_page(
            sample_parsed_file,
            sample_graph,
            graph_metrics["pagerank"],
            graph_metrics["betweenness"],
            graph_metrics["community"],
            sample_source_bytes,
        ),
        await generator.generate_module_page("A", "python", [], nx.DiGraph(), target_path="a"),
        await outage_generator.generate_module_page(
            "B", "python", [], nx.DiGraph(), target_path="b"
        ),
    ]

    assert len({p.confidence for p in pages}) == 3
    assert min(p.confidence for p in pages) < BANNER_THRESHOLD
