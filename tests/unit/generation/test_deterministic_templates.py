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
        # Exactly the shape KnowledgeGraphContext.get_file_context builds
        # (order/title/description). Inventing a shape here is how the first
        # version of this template shipped with a field that never exists.
        tour_steps=[{"order": 1, "title": "Parsing", "description": "Where parsing starts."}],
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
    assert "Step 1: Parsing. Where parsing starts." in page.content
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


def test_symbol_spotlight_renders(generator):
    """Not reachable through generate_all (deterministic mode drops the
    spotlight bucket), but the renderer stays for on-demand generation, so
    it needs coverage of its own."""
    from repowise.core.generation.context_assembler import SymbolSpotlightContext

    ctx = SymbolSpotlightContext(
        symbol_name="parse_file",
        qualified_name="parser.ASTParser.parse_file",
        kind="method",
        signature="def parse_file(self, fi, src) -> ParsedFile",
        docstring="Parse one file into an AST.",
        file_path="src/parser.py",
        decorators=["@lru_cache"],
        is_async=False,
        complexity_estimate=7,
        callers=["src/pipeline.py"],
        source_body="def parse_file(self, fi, src):\n    return ...",
    )
    page = generator._deterministic_symbol_spotlight(
        ctx, "src/parser.py::parse_file", "Symbol: parser.ASTParser.parse_file"
    )

    assert page.page_type == "symbol_spotlight"
    assert page.provider_name == "template"
    assert "Parse one file into an AST." in page.content
    assert "@lru_cache" in page.content
    # Callers are module importers, not verified call sites. The page must
    # not claim more than the graph knows.
    assert "not confirmed call sites" in page.content


def test_symbol_spotlight_tolerates_missing_kind(generator):
    from repowise.core.generation.context_assembler import SymbolSpotlightContext

    ctx = SymbolSpotlightContext(
        symbol_name="x",
        qualified_name="x",
        kind="",
        signature="",
        docstring=None,
        file_path="a.py",
        decorators=[],
        is_async=False,
        complexity_estimate=0,
        callers=[],
    )
    page = generator._deterministic_symbol_spotlight(ctx, "a.py::x", "Symbol: x")
    assert "is a symbol defined in" in page.content


def test_multiline_summaries_stay_inside_their_list_item(generator):
    """A raw newline in a bullet ends the markdown list, dumping the rest as
    body text. Page summaries are routinely multi-paragraph, so every text
    field folded into a list item runs through the oneline filter."""
    ctx = LayerPageContext(
        layer_name="Core",
        layer_id="layer:core",
        layer_description="",
        file_count=3,
        key_files=[
            {
                "path": "a.py",
                "role": "internal",
                "summary": "First line.\n\nSecond paragraph that would break the list.",
            },
            {"path": "b.py", "role": "internal", "summary": "Short."},
        ],
    )
    page = generator._deterministic_layer_page(ctx, "Layer: Core")

    body = page.content.split("## Key files", 1)[1]
    bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 2, f"list broke apart: {bullets}"
    assert "First line. Second paragraph" in bullets[0]


def test_summary_skips_the_stats_line():
    """Several templates open with a bold field line under the H1.

    The persisted summary is what the wiki list, search results and
    get_context show, so a summary of "**Files:** 412 | **Lines:** 90210"
    displaces the sentence that would have told the reader what the page is.
    """
    from repowise.core.generation.page_generator.helpers import _extract_summary

    content = (
        "# Module: core/ingestion\n\n"
        "**Files:** 412 | **Lines:** 90210\n\n"
        "## Overview\n\n"
        "Walks the repository and parses every source file it recognises.\n"
    )
    assert _extract_summary(content, skip_metadata=True).startswith("Walks the repository")
    # Off by default: a model that opens with **Purpose:** means it as prose.
    assert _extract_summary(content).startswith("**Files:**")


# ---------------------------------------------------------------------------
# Docstring and signature rendering
# ---------------------------------------------------------------------------


def test_as_markdown_converts_sphinx_roles_to_code_spans():
    from repowise.core.generation.page_generator.deterministic import as_markdown

    out = as_markdown("See :meth:`Store.get` and :class:`~pkg.Thing`.")
    assert out == "See `Store.get` and `pkg.Thing`."


def test_as_markdown_converts_double_backtick_literals():
    from repowise.core.generation.page_generator.deterministic import as_markdown

    assert as_markdown("Pass ``None`` to skip.") == "Pass `None` to skip."


def test_as_markdown_drops_rest_directives():
    from repowise.core.generation.page_generator.deterministic import as_markdown

    out = as_markdown("Body text.\n\n.. note:: internal only\n")
    assert ".. note::" not in out
    assert "Body text." in out


def test_as_markdown_dedents_so_the_body_is_not_a_code_block():
    """Four leading spaces would make markdown render the body as code."""
    from repowise.core.generation.page_generator.deterministic import as_markdown

    out = as_markdown("Summary line.\n\n    Indented continuation.\n")
    assert "\n    Indented" not in out
    assert "Indented continuation." in out


def test_as_markdown_leaves_plain_text_alone():
    from repowise.core.generation.page_generator.deterministic import as_markdown

    assert as_markdown("Just a sentence.") == "Just a sentence."
    assert as_markdown(None) == ""


def test_signature_collapses_source_whitespace():
    """Signatures span source lines, so raw text carries runs of indentation."""
    from repowise.core.generation.page_generator.deterministic import signature

    raw = "def go(\n        a: int,\n        b: str,\n    ) -> None"
    assert signature(raw) == "def go( a: int, b: str, ) -> None"


def test_signature_truncates_at_an_argument_boundary_not_mid_token():
    from repowise.core.generation.page_generator.deterministic import signature

    raw = "def go(" + ", ".join(f"argument_number_{i}: int = 0" for i in range(20)) + ") -> None"
    out = signature(raw, limit=80)
    assert out.endswith(" …")
    # The visible tail must be a whole parameter, never half an identifier.
    assert not out.rstrip(" …").rstrip(",").endswith("argument_number")
    assert "argument_number_0: int = 0" in out


def test_signature_leaves_short_signatures_untouched():
    from repowise.core.generation.page_generator.deterministic import signature

    assert signature("def go(x: int) -> None") == "def go(x: int) -> None"
