"""The architecture map is part of the repository overview.

It used to have a page of its own that described the same repository at the
same altitude and in the same words as the overview — the two shared roughly a
quarter of their vocabulary, so a reader who met both read one thing twice.
The diagram was the only thing that page uniquely had, so it moved here.

The map is the deterministic graph-derived one, not a diagram the model drew,
and it has to survive every path the overview can be produced by: the model
path, the deterministic path, and the fallback taken when the provider fails.
Losing it on the fallback would mean a provider outage silently costs the wiki
its architecture diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.mock import MockProvider

_MERMAID = "graph TD\n  A[cli] --> B[core]\n  B --> C[server]"


class _OutageProvider(MockProvider):
    """A provider that fails the way a real outage does: every call raises."""

    async def generate(self, *args, **kwargs):
        raise RuntimeError("upstream 529 overloaded")


@dataclass
class _Case:
    name: str
    gen: PageGenerator


@pytest.fixture
def model_gen(sample_config) -> PageGenerator:
    return PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)


@pytest.fixture
def outage_gen(sample_config) -> PageGenerator:
    return PageGenerator(_OutageProvider(), ContextAssembler(sample_config), sample_config)


@pytest.fixture
def deterministic_gen(sample_config) -> PageGenerator:
    cfg = replace(sample_config, deterministic=True)
    return PageGenerator(MockProvider(), ContextAssembler(cfg), cfg)


async def _overview(
    gen: PageGenerator, sample_repo_structure, mermaid: str | None
) -> GeneratedPage:
    return await gen.generate_repo_overview(
        sample_repo_structure,
        pagerank={},
        sccs=[],
        community={},
        repo_name="demo",
        overview_mermaid=mermaid,
    )


async def test_model_written_overview_carries_the_map(model_gen, sample_repo_structure):
    page = await _overview(model_gen, sample_repo_structure, _MERMAID)
    assert "## Architecture map" in page.content
    assert "```mermaid" in page.content
    assert "A[cli] --> B[core]" in page.content


async def test_deterministic_overview_carries_the_map(deterministic_gen, sample_repo_structure):
    page = await _overview(deterministic_gen, sample_repo_structure, _MERMAID)
    assert "```mermaid" in page.content
    assert "A[cli] --> B[core]" in page.content


async def test_provider_outage_keeps_the_map(outage_gen, sample_repo_structure):
    """A provider outage costs the prose around the diagram, never the diagram."""
    page = await _overview(outage_gen, sample_repo_structure, _MERMAID)
    assert "```mermaid" in page.content
    assert "A[cli] --> B[core]" in page.content


async def test_embedding_is_idempotent(model_gen, sample_repo_structure):
    """Reused and cached pages are re-embedded on every run; that must be a no-op."""
    page = await _overview(model_gen, sample_repo_structure, _MERMAID)
    assert page.content.count("```mermaid") == 1


async def test_no_map_available_leaves_the_page_alone(model_gen, sample_repo_structure):
    """An empty map must not stamp an empty diagram block onto the page."""
    page = await _overview(model_gen, sample_repo_structure, "")
    assert "```mermaid" not in page.content
    assert page.content.strip()


async def test_overview_prompt_includes_repository_source_evidence(
    model_gen, sample_repo_structure
):
    config = replace(
        model_gen._config,
        source_evidence_files={
            "repo_overview": ("README.md", "docs/ARCHITECTURE.md"),
        },
    )
    generator = PageGenerator(model_gen._provider, ContextAssembler(config), config)
    await generator.generate_repo_overview(
        sample_repo_structure,
        pagerank={},
        sccs=[],
        community={},
        repo_name="demo",
        source_map={
            "README.md": b"Demo turns source archives into indexed documentation.",
            "docs/ARCHITECTURE.md": b"The parser feeds a graph and then a wiki generator.",
        },
    )

    prompt = generator._provider.calls[-1]["user_prompt"]
    assert "## Authoritative repository evidence" in prompt
    assert '<repository-file path="README.md">' in prompt
    assert "turns source archives into indexed documentation" in prompt
    assert '<repository-file path="docs/ARCHITECTURE.md">' in prompt
