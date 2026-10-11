"""A page says how far it can be trusted, and the three answers are different.

``confidence`` stood at a constant 1.0 on every page a run produced. A constant
column cannot gate anything: retrieval cannot weight by it, the reader's
low-confidence banner had never once rendered, and — the case that matters — a
wiki where a provider outage left hundreds of stubs looked exactly as
trustworthy as a complete one.

Three values, because three distinctions are actually available when a page is
written:

* a template page is everything the parse, the graph and git history know, with
  no model in the loop, including the deterministic rendering of a
  model-written type by a keyless or ``--no-prose`` run;
* a model page is grounded in that material and checked against it, but it is a
  summary of the code rather than an extraction from it;
* a stub the run substituted for a model page whose provider call *failed* is
  that same material standing in for prose that was attempted and lost. That is
  the one a reader cannot infer from the page, so it is the only one flagged.

The third bullet used to cover the keyless render too, and that was the bug: a
keyless run produces every model-written page this way, so the reader's warning
landed on the repository overview and all of its subsystem pages at once, on
wikis with nothing wrong with them. ``confidence`` is a trust axis; whether a
model has written a page yet is ``provider_name``, and the two must not be read
off one number.
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

# There is deliberately no threshold constant here any more.
#
# This file used to carry a hand-copied ``BANNER_THRESHOLD = 0.5``, on the
# reasoning that what mattered about the three values was which side of the
# reader's warning they fell on. That warning is gone: the reader now keys its
# one caveat on the ``stub_fallback_error`` marker, because a number could not
# tell a failed page from a deterministic one. Nothing in the product reads
# 0.5, so asserting against it would be asserting against a fiction.
#
# What is left to protect is the ordering and the identities, which is what
# these assert.


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


async def test_a_model_written_page_is_confident_but_not_certain(generator):
    page = await generator.generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.confidence == MODEL_PAGE_CONFIDENCE
    assert page.confidence < TEMPLATE_PAGE_CONFIDENCE
    # A model page is normal, not suspect: it sits above the failure value.
    assert page.confidence > STUB_PAGE_CONFIDENCE


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
    assert page.confidence < MODEL_PAGE_CONFIDENCE


def _deterministic_generator() -> PageGenerator:
    config = GenerationConfig(
        max_tokens=1024, token_budget=2000, max_concurrency=2, deterministic=True
    )
    return PageGenerator(MockProvider(), ContextAssembler(config), config)


async def test_a_keyless_page_is_not_flagged():
    """The regression this file now guards.

    No provider is configured, so no model was asked and none failed. What
    renders is the deterministic page: every statement came from the index,
    which is the template claim exactly. Flagging it told a keyless user their
    whole wiki was doubtful, about an index that is that shape by design.
    """
    page = await _deterministic_generator().generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.confidence == TEMPLATE_PAGE_CONFIDENCE
    # Nothing was attempted, so there is no failure to record. This is the key
    # the reader keys its caveat on, and it must stay absent here.
    assert STUB_FALLBACK_ERROR not in page.metadata


async def test_the_two_stub_kinds_are_distinguishable(outage_generator):
    """Same template, same provider name, opposite claims.

    The bytes cannot tell these apart, so if confidence agreed too there would
    be nothing left that could. Asserted together rather than apart because the
    failure mode is the pair collapsing, which either test alone still passes.
    """
    keyless = await _deterministic_generator().generate_module_page(
        "A", "python", [], nx.DiGraph(), target_path="a"
    )
    lost = await outage_generator.generate_module_page(
        "B", "python", [], nx.DiGraph(), target_path="b"
    )

    assert keyless.provider_name == lost.provider_name == "template"
    assert keyless.confidence != lost.confidence
    assert keyless.confidence > lost.confidence


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
    assert min(p.confidence for p in pages) == STUB_PAGE_CONFIDENCE


async def test_the_overview_keeps_its_value_through_post_processing(
    outage_generator, sample_repo_structure
):
    """The value has to still be there after the wrappers have run.

    ``_stub_fallback`` sets confidence and hands the page on; the overview then
    goes through more post-processing than any other page type: the package
    table, the capability table, the architecture map and the source-evidence
    pass all touch the page afterwards. None of them rewrites ``confidence``
    today, and this is what says so: a wrapper that rebuilt the page instead of
    mutating it would silently restore the default, putting a failed page back
    at full confidence with its failure marker still attached.
    """
    page = await outage_generator.generate_repo_overview(
        sample_repo_structure,
        pagerank={},
        sccs=[],
        community={},
        repo_name="demo",
    )

    assert page.metadata[STUB_FALLBACK_ERROR]
    assert page.confidence == STUB_PAGE_CONFIDENCE
