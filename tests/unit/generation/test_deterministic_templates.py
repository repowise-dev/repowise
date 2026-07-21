"""Render tests for deterministic templates the sample repo cannot reach.

``tests/integration/test_deterministic_generation.py`` covers the page types
the fixture repo actually produces. Four templates are unreachable there: the
layer page needs a knowledge graph, and three onboarding slots gate themselves
off on a fixture with no git history and no dependency manifest. Rendering
them here keeps a Jinja typo from shipping unnoticed.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.context_assembler import ContextAssembler, LayerPageContext
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.onboarding.subkinds.active_landscape import (
    ActiveLandscapeContext,
    HotDir,
    HotFile,
)
from repowise.core.generation.onboarding.subkinds.development_guide import (
    DevelopmentGuideContext,
    SuffixPattern,
)
from repowise.core.generation.onboarding.subkinds.development_guide import (
    # Aliased so pytest does not try to collect it as a test class.
    TestMirror as _TestMirror,
)
from repowise.core.generation.onboarding.subkinds.getting_started import (
    GettingStartedContext,
    ReadmeSection,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.template import TemplateProvider


@pytest.fixture
def generator() -> PageGenerator:
    config = GenerationConfig(deterministic=True)
    return PageGenerator(TemplateProvider(), ContextAssembler(config), config)


def test_layer_page_renders(generator):
    ctx = LayerPageContext(
        layer_name="Ingestion",
        layer_id="layer:ingestion",
        layer_description="Parses source files into ASTs.",
        file_count=7,
        key_files=[{"path": "src/parse.py", "role": "entry_point", "summary": "Parses files."}],
        deps_out=[{"target_layer": "Storage", "edge_count": 4}],
        deps_in=[{"source_layer": "CLI", "edge_count": 2}],
        tour_steps=[{"order": 1, "target_path": "src/parse.py", "reason": "Where parsing starts."}],
        entry_points=["src/parse.py"],
        edge_connectors=["src/io.py"],
        diagram_mermaid="flowchart LR\n  a --> b",
    )
    page = generator._deterministic_layer_page(ctx, "Layer: Ingestion")

    assert page.page_id == "layer_page:layer:ingestion"
    assert page.provider_name == "template"
    assert page.input_tokens == 0
    assert "```mermaid" in page.content
    assert "flowchart LR" in page.content
    assert "**Storage**" in page.content
    assert "**CLI**" in page.content
    # Sits between two layers, so the overview should say so rather than
    # calling it foundational or top-of-stack.
    assert "mid-stack" in page.content
    assert "Generated deterministically" in page.content


def test_layer_page_without_diagram_renders(generator):
    """A layer with no KG diagram must still produce a usable page."""
    ctx = LayerPageContext(
        layer_name="Utilities",
        layer_id="layer:utilities",
        layer_description="",
        file_count=3,
    )
    page = generator._deterministic_layer_page(ctx, "Layer: Utilities")

    assert "```mermaid" not in page.content
    assert "stands on its own" in page.content


def _onboarding_spec(slot: str, template: str):
    from repowise.core.generation.onboarding.registry import SubkindSpec

    return SubkindSpec(
        slot=slot,
        title=slot.replace("_", " ").title(),
        template=template,
        build_context=lambda _s: None,
    )


def test_getting_started_renders(generator):
    ctx = GettingStartedContext(
        repo_name="demo",
        package_managers=["uv"],
        runtime_dependencies=[{"name": "httpx", "version": "0.27"}],
        dev_dependencies=[{"name": "pytest", "version": "9.0"}],
        readme_sections=[ReadmeSection(heading="Install", body="Run `uv sync`.")],
        entry_points=["src/main.py"],
    )
    page = generator._deterministic_onboarding_page(
        _onboarding_spec("getting_started", "getting_started.j2"), ctx, "onboarding/getting_started"
    )

    assert page.metadata["onboarding_slot"] == "getting_started"
    assert "**uv**" in page.content
    assert "Run `uv sync`." in page.content
    assert "`httpx`" in page.content


def test_development_guide_renders(generator):
    ctx = DevelopmentGuideContext(
        repo_name="demo",
        suffix_patterns=[SuffixPattern(suffix="_cmd.py", examples=["a_cmd.py"], file_count=4)],
        test_mirror=_TestMirror(test_root="tests", matched_files=12, source_files=20),
        parallel_dirs=["packages/core", "packages/cli"],
        entry_points=["src/main.py"],
    )
    page = generator._deterministic_onboarding_page(
        _onboarding_spec("development_guide", "development_guide.j2"),
        ctx,
        "onboarding/development_guide",
    )

    assert "`_cmd.py`" in page.content
    assert "tests" in page.content
    assert "12 of 20" in page.content


def test_active_landscape_renders(generator):
    ctx = ActiveLandscapeContext(
        repo_name="demo",
        total_commits_90d=120,
        files_touched_90d=45,
        hot_files=[
            HotFile(
                path="src/core.py",
                commit_count_90d=30,
                primary_owner="Ada",
                is_hotspot=True,
                age_days=400,
            )
        ],
        hot_dirs=[HotDir(path="src", total_commits_90d=90, hotspot_count=2, file_count=20)],
        dead_code_in_hot_files=[{"symbol_name": "old_fn", "file_path": "src/core.py"}],
        stable_file_count=8,
    )
    page = generator._deterministic_onboarding_page(
        _onboarding_spec("active_landscape", "active_landscape.j2"),
        ctx,
        "onboarding/active_landscape",
    )

    assert "120 commits touched 45 files" in page.content
    assert "`src/core.py`" in page.content
    assert "Ada" in page.content
    assert "old_fn" in page.content
