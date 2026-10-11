"""What a wiki page says about itself, and what a model is told to write.

* The module prompt carries no dead-code findings: a "potentially unused"
  list handed to the model came back as confident prose about deletions.
* A file page's own vocabulary is embedded for search but not rendered:
  it lives in page metadata, and the one embed recipe reads it from there.
* The footer claims only what the page was built from.
* A template page credits no model; a model-written page names its model.
"""

from __future__ import annotations

import json

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.persistence.vector_store import FILE_VOCABULARY_KEY, embed_item
from repowise.core.providers.llm.mock import MockProvider

DEAD_CODE = [
    {
        "symbol_name": "legacy_resolver",
        "reason": "no callers found",
        "confidence": 0.9,
        "safe_to_delete": True,
    }
]

SOURCE = b'''\
class Calculator:
    """Adds two integers for the billing ledger."""

    def add(self, a: int, b: int) -> int:
        raise ValueError("ledger overflow while adding")
'''


def _generator(provider=None, **config) -> PageGenerator:
    cfg = GenerationConfig(max_tokens=1024, token_budget=2000, max_concurrency=2, **config)
    return PageGenerator(provider or MockProvider(), ContextAssembler(cfg), cfg)


async def _file_page(generator, parsed, graph, metrics):
    return await generator.generate_file_page(
        parsed,
        graph,
        metrics["pagerank"],
        metrics["betweenness"],
        metrics["community"],
        SOURCE,
    )


async def test_the_module_prompt_has_no_dead_code_section():
    provider = MockProvider()
    await _generator(provider).generate_module_page(
        "Resolution Layer",
        "python",
        [],
        nx.DiGraph(),
        dead_code_findings=DEAD_CODE,
        target_path="core/resolvers",
    )

    prompt = provider.calls[-1]["user_prompt"]
    assert "Potentially unused code" not in prompt
    assert "legacy_resolver" not in prompt


async def test_the_file_vocabulary_is_in_metadata_not_on_the_page(
    sample_parsed_file, sample_graph, graph_metrics
):
    page = await _file_page(_generator(), sample_parsed_file, sample_graph, graph_metrics)

    vocabulary = page.metadata[FILE_VOCABULARY_KEY]
    assert "ledger overflow while adding" in vocabulary
    assert "## In the code" not in page.content
    assert "ledger overflow while adding" not in page.content


async def test_the_embed_recipe_picks_the_vocabulary_up(
    sample_parsed_file, sample_graph, graph_metrics
):
    """Both shapes a writer holds: a generated page's dict, a stored row's JSON."""
    page = await _file_page(_generator(), sample_parsed_file, sample_graph, graph_metrics)
    fields = dict(
        title=page.title,
        page_type=page.page_type,
        target_path=page.target_path,
        summary=page.summary,
        content=page.content,
    )

    for metadata in (page.metadata, json.dumps(page.metadata)):
        _pid, text, _meta = embed_item(page.page_id, page_metadata=metadata, **fields)
        assert "ledger overflow while adding" in text

    _pid, bare, _meta = embed_item(page.page_id, **fields)
    assert "ledger overflow while adding" not in bare


@pytest.mark.parametrize(
    ("language", "footer", "retired"),
    [
        (None, "Generated from parsed code, the import graph and git history.", "checked against"),
        ("de", "Erstellt aus geparstem Code, dem Importgraphen und der Git-Historie.", "geprüft"),
    ],
)
async def test_the_footer_claims_only_its_inputs(
    language, footer, retired, sample_parsed_file, sample_graph, graph_metrics
):
    generator = _generator(language=language) if language else _generator()
    page = await _file_page(generator, sample_parsed_file, sample_graph, graph_metrics)

    assert footer in page.content
    assert retired not in page.content


async def test_a_template_page_credits_no_model(sample_parsed_file, sample_graph, graph_metrics):
    generator = _generator(MockProvider(model="real-model-7"))
    page = await _file_page(generator, sample_parsed_file, sample_graph, graph_metrics)

    assert page.provider_name == "template"
    assert page.model_name == ""


async def test_a_model_written_page_names_its_model():
    page = await _generator(MockProvider(model="real-model-7")).generate_module_page(
        "Resolution Layer", "python", [], nx.DiGraph(), target_path="core/resolvers"
    )

    assert page.provider_name == "mock"
    assert page.model_name == "real-model-7"
