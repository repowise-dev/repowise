"""Render tests for deterministic templates the sample repo cannot reach.

``tests/integration/test_deterministic_generation.py`` covers the page types
the fixture repo actually produces. The rest are unreachable there: the layer
page needs a knowledge graph, and the onboarding slots below gate themselves
off on a fixture with no git history and no dependency manifest. Rendering
them here keeps a Jinja typo from shipping unnoticed.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.context_assembler import ApiContractContext, ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.onboarding.subkinds.active_landscape import (
    ActiveLandscapeContext,
    HotDir,
    HotFile,
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
    page = generator._stub_onboarding_page(
        _onboarding_spec("getting_started", "getting_started.j2"), ctx, "onboarding/getting_started"
    )

    assert page.metadata["onboarding_slot"] == "getting_started"
    assert "**uv**" in page.content
    assert "Run `uv sync`." in page.content
    assert "`httpx`" in page.content


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
    page = generator._stub_onboarding_page(
        _onboarding_spec("active_landscape", "active_landscape.j2"),
        ctx,
        "onboarding/active_landscape",
    )

    assert "120 commits touched 45 files" in page.content
    assert "`src/core.py`" in page.content
    assert "Ada" in page.content
    assert "old_fn" in page.content


def test_symbol_spotlight_renders(generator):
    """The only renderer for this page type, so it always runs."""
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
    page = generator._structural_symbol_spotlight(
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
    page = generator._structural_symbol_spotlight(ctx, "a.py::x", "Symbol: x")
    assert "is a symbol defined in" in page.content


def test_multiline_summaries_stay_inside_their_list_item(generator):
    """A raw newline in a bullet ends the markdown list, dumping the rest as
    body text. Parsed signatures routinely carry newlines, so every text field
    folded into a list item runs through the oneline filter."""
    ctx = ApiContractContext(
        file_path="api/routes.py",
        language="python",
        raw_content="",
        endpoints=[
            "def create(\n    self,\n    payload: dict,\n) -> Response",
            "def delete(self, id: str) -> None",
        ],
        schemas=[],
    )
    page = generator._structural_api_contract(ctx, "api/routes.py", "API Contract: api/routes.py")

    body = page.content.split("## Operations", 1)[1]
    bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 2, f"list broke apart: {bullets}"
    assert "def create( self, payload: dict, ) -> Response" in bullets[0]


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
    from repowise.core.generation.page_generator.structural import as_markdown

    out = as_markdown("See :meth:`Store.get` and :class:`~pkg.Thing`.")
    assert out == "See `Store.get` and `pkg.Thing`."


def test_as_markdown_converts_double_backtick_literals():
    from repowise.core.generation.page_generator.structural import as_markdown

    assert as_markdown("Pass ``None`` to skip.") == "Pass `None` to skip."


def test_as_markdown_drops_rest_directives():
    from repowise.core.generation.page_generator.structural import as_markdown

    out = as_markdown("Body text.\n\n.. note:: internal only\n")
    assert ".. note::" not in out
    assert "Body text." in out


def test_as_markdown_dedents_so_the_body_is_not_a_code_block():
    """Four leading spaces would make markdown render the body as code."""
    from repowise.core.generation.page_generator.structural import as_markdown

    out = as_markdown("Summary line.\n\n    Indented continuation.\n")
    assert "\n    Indented" not in out
    assert "Indented continuation." in out


def test_as_markdown_leaves_plain_text_alone():
    from repowise.core.generation.page_generator.structural import as_markdown

    assert as_markdown("Just a sentence.") == "Just a sentence."
    assert as_markdown(None) == ""


def test_signature_collapses_source_whitespace():
    """Signatures span source lines, so raw text carries runs of indentation."""
    from repowise.core.generation.page_generator.structural import signature

    raw = "def go(\n        a: int,\n        b: str,\n    ) -> None"
    assert signature(raw) == "def go( a: int, b: str, ) -> None"


def test_signature_truncates_at_an_argument_boundary_not_mid_token():
    from repowise.core.generation.page_generator.structural import signature

    raw = "def go(" + ", ".join(f"argument_number_{i}: int = 0" for i in range(20)) + ") -> None"
    out = signature(raw, limit=80)
    assert out.endswith(" …")
    # The visible tail must be a whole parameter, never half an identifier.
    assert not out.rstrip(" …").rstrip(",").endswith("argument_number")
    assert "argument_number_0: int = 0" in out


def test_signature_leaves_short_signatures_untouched():
    from repowise.core.generation.page_generator.structural import signature

    assert signature("def go(x: int) -> None") == "def go(x: int) -> None"


def test_as_markdown_leaves_fenced_code_blocks_intact():
    """The double-backtick rule must not chew the fence delimiters."""
    from repowise.core.generation.page_generator.structural import as_markdown

    src = "Run it.\n\nExample:\n\n```python\nx = 1\n```\n"
    assert "```python\nx = 1\n```" in as_markdown(src)


def test_as_markdown_leaves_triple_backtick_spans_intact():
    from repowise.core.generation.page_generator.structural import as_markdown

    assert as_markdown("Use ```code``` here.") == "Use ```code``` here."


def test_as_markdown_does_not_eat_ordinary_colon_text():
    """A permissive role pattern deletes any word:word: before a backtick."""
    from repowise.core.generation.page_generator.structural import as_markdown

    assert as_markdown("Time complexity: O(n:m:`k`)") == "Time complexity: O(n:m:`k`)"
    assert as_markdown("See http://x.io/a:b:`c`") == "See http://x.io/a:b:`c`"


def test_as_markdown_removes_a_directive_body_not_just_its_head():
    """A dangling indented body renders as a code block, the thing we avoid."""
    from repowise.core.generation.page_generator.structural import as_markdown

    out = as_markdown(
        "Summary line.\n\n    Longer prose.\n\n    .. note::\n\n        Internal only.\n\n    More prose.\n"
    )
    assert "Internal only." not in out
    assert "Longer prose." in out and "More prose." in out


def test_as_markdown_is_idempotent():
    from repowise.core.generation.page_generator.structural import as_markdown

    for src in (
        "See :meth:`Store.get`.",
        "Pass ``None``.",
        "Fence:\n\n```py\nx=1\n```\n",
        "Body.\n\n.. note:: hi\n",
    ):
        once = as_markdown(src)
        assert as_markdown(once) == once


# ---------------------------------------------------------------------------
# The concept page's own header
# ---------------------------------------------------------------------------


def _module_ctx(assembler, files):
    from repowise.core.generation.context_assembler import FilePageContext

    contexts = [
        FilePageContext(
            file_path=f,
            language="python",
            symbols=[],
            docstring=None,
            imports=[],
            exports=[],
            dependencies=[],
            dependents=[],
            pagerank_score=0.1,
            betweenness_score=0.0,
            is_entry_point=False,
            community_id=None,
            is_api_contract=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=10,
        )
        for f in files
    ]
    return assembler.assemble_module_page("Ingestion Pipeline", "python", contexts, None)


def test_module_page_leads_with_the_concept_title(generator):
    """The H1 is the group's name, and the directories sit under it.

    A concept page covers several directories, so the old ``# Module: <path>``
    heading had nothing honest to put after the colon. The title says what the
    group is; the line beneath says where to go and look.
    """
    ctx = _module_ctx(generator._assembler, ["src/ingest/read.py", "src/parse/ast.py"])
    page = generator._stub_module_page(ctx, "src/ingest", "Ingestion Pipeline", None)

    assert page.title == "Ingestion Pipeline"
    assert page.content.startswith("# Ingestion Pipeline\n")
    assert "Module:" not in page.content
    assert "`src/ingest`" in page.content and "`src/parse`" in page.content


def test_module_page_of_root_files_says_so(generator):
    """Files at the repository root have no directory to name.

    ``PurePosixPath("setup.py").parent`` is ``"."``, and printing a bare dot
    where a path belongs reads as a bug. Root-level files are a real case:
    the grouper anchors a group there whenever a repository keeps code at the
    top level.
    """
    ctx = _module_ctx(generator._assembler, ["setup.py", "main.py"])
    page = generator._stub_module_page(ctx, "root", "Project Entry Points", None)

    assert "Repository root" in page.content
    assert "`.`" not in page.content
