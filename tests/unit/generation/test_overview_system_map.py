"""The system map is part of the repository overview.

It is built from structure and has to survive every path the overview can be
produced by: the model path, the deterministic path, and the fallback taken
when the provider fails. Losing it on the fallback would mean a provider
outage silently costs the wiki its diagram. A model only renames what the
structure drew, through a JSON block that never reaches the page.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from repowise.core.generation.architecture_map import SystemMap, build_system_map
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.generation.page_generator.pertype import SYSTEM_MAP_NAMES
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider


def _map() -> SystemMap:
    return build_system_map(
        {"cli/main.py": "python", "core/engine.py": "python", "core/store.py": "python"},
        [("cli/main.py", "core/engine.py", "imports"), ("core/engine.py", "core/store.py", "calls")],
        repo_name="demo",
        manifests=["cli/pyproject.toml", "core/pyproject.toml"],
        entry_points=["cli/main.py"],
    )


_NAMED_REPLY = (
    "## Project Summary\n\nDemo does things.\n\n## Architecture\n\nParts.\n\n"
    "```json\n"
    '{"caption": "Commands reach the engine.", '
    '"nodes": [{"id": "n_core_engine_py", "label": "Processing engine", "role": "Does the work"}], '
    '"edges": [{"from": "n_cli", "to": "n_core_engine_py", "verb": "drives"}]}\n'
    "```\n"
)


class _OutageProvider(MockProvider):
    """A provider that fails the way a real outage does: every call raises."""

    async def generate(self, *args, **kwargs):
        raise RuntimeError("upstream 529 overloaded")


@pytest.fixture
def model_gen(sample_config) -> PageGenerator:
    return PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)


@pytest.fixture
def naming_gen(sample_config) -> PageGenerator:
    provider = MockProvider(responses=[GeneratedResponse(_NAMED_REPLY, 100, 50)])
    return PageGenerator(provider, ContextAssembler(sample_config), sample_config)


@pytest.fixture
def outage_gen(sample_config) -> PageGenerator:
    return PageGenerator(_OutageProvider(), ContextAssembler(sample_config), sample_config)


@pytest.fixture
def deterministic_gen(sample_config) -> PageGenerator:
    cfg = replace(sample_config, deterministic=True)
    return PageGenerator(MockProvider(), ContextAssembler(cfg), cfg)


async def _overview(
    gen: PageGenerator, sample_repo_structure, system_map: SystemMap | None
) -> GeneratedPage:
    return await gen.generate_repo_overview(
        sample_repo_structure,
        pagerank={},
        sccs=[],
        community={},
        repo_name="demo",
        system_map=system_map,
    )


async def test_model_written_overview_carries_the_map(model_gen, sample_repo_structure):
    page = await _overview(model_gen, sample_repo_structure, _map())
    assert "## System map" in page.content
    assert 'n_cli -->|"imports"| n_core_engine_py' in page.content
    prompt = model_gen._provider.calls[-1]["user_prompt"]
    assert "### System map to name" in prompt and '"id": "n_core_engine_py"' in prompt


async def test_model_names_reach_the_map_and_the_block_does_not(naming_gen, sample_repo_structure):
    page = await _overview(naming_gen, sample_repo_structure, _map())
    assert "<b>Processing engine</b>" in page.content
    assert 'n_cli -->|"drives"| n_core_engine_py' in page.content
    assert "Commands reach the engine." in page.content
    assert "```json" not in page.content
    assert page.content.index("## System map") < page.content.index("## Architecture")


async def test_deterministic_overview_carries_the_map(deterministic_gen, sample_repo_structure):
    page = await _overview(deterministic_gen, sample_repo_structure, _map())
    assert "```mermaid" in page.content
    assert 'n_cli -->|"imports"| n_core_engine_py' in page.content


async def test_provider_outage_keeps_the_map(outage_gen, sample_repo_structure):
    """A provider outage costs the prose around the diagram, never the diagram."""
    page = await _overview(outage_gen, sample_repo_structure, _map())
    assert "```mermaid" in page.content
    assert 'n_cli -->|"imports"| n_core_engine_py' in page.content


async def test_one_map_per_page(model_gen, sample_repo_structure):
    page = await _overview(model_gen, sample_repo_structure, _map())
    assert page.content.count("```mermaid") == 1


async def test_no_map_available_leaves_the_page_alone(model_gen, sample_repo_structure):
    """No structure to draw must not stamp an empty diagram block onto the page."""
    page = await _overview(model_gen, sample_repo_structure, None)
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
    assert "## Additional repository evidence" in prompt
    assert '<repository-file path="README.md">' in prompt
    assert "turns source archives into indexed documentation" in prompt
    assert '<repository-file path="docs/ARCHITECTURE.md">' in prompt


async def test_a_reused_overview_keeps_its_names_and_recompiles_the_map(
    naming_gen, sample_config, sample_repo_structure
):
    """A cache hit makes no call, keeps the stored names, and draws today's structure."""
    first = await _overview(naming_gen, sample_repo_structure, _map())
    assert first.metadata[SYSTEM_MAP_NAMES]["nodes"][0]["label"] == "Processing engine"
    provider = MockProvider()
    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            model_name=provider.model_name,
            content=first.content.replace('n_cli -->|"drives"|', 'n_cli -->|"stale"|'),
            metadata=first.metadata,
        )
    }
    reused = PageGenerator(
        provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    page = await _overview(reused, sample_repo_structure, _map())
    assert provider.call_count == 0
    assert 'n_cli -->|"drives"| n_core_engine_py' in page.content
    assert "<b>Processing engine</b>" in page.content
    assert page.content.count("```mermaid") == 1


async def test_outage_reuses_the_stored_names(sample_config, sample_repo_structure):
    prior = {
        "repo_overview:demo": PriorPage(
            source_hash="other",
            model_name="mock",
            content="",
            metadata={SYSTEM_MAP_NAMES: {"nodes": [{"id": "n_cli", "label": "Command line"}]}},
        )
    }
    gen = PageGenerator(
        _OutageProvider(), ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    page = await _overview(gen, sample_repo_structure, _map())
    assert "<b>Command line</b>" in page.content


async def test_a_keyless_render_keeps_the_stored_names(sample_config, sample_repo_structure):
    names = {"nodes": [{"id": "n_cli", "label": "Command line"}]}
    prior = {
        "repo_overview:demo": PriorPage(
            source_hash="other", model_name="mock", content="", metadata={SYSTEM_MAP_NAMES: names}
        )
    }
    cfg = replace(sample_config, deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(cfg), cfg, prior_pages=prior)
    page = await _overview(gen, sample_repo_structure, _map())
    assert page.metadata[SYSTEM_MAP_NAMES] == names
