"""The overview's package table.

The table is built from the run's own facts and embedded after the page is
written, on both the model-written and the structure-only page. That is
deliberate: the page's other enumerable facts came back from the model, so they
were resampled on every render — two calls to the same model with the same
prompt and the same temperature disagreed on how many rows the page had and on
which paths it cited. A reader comparing two updates could not tell a code
change from a re-roll. Facts that the run already knows do not go through a
sampler.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.overview_tables import build_package_table, embed_package_table
from repowise.core.ingestion.models import PackageInfo, RepoStructure

from .conftest import _make_file_info


def _count_path_citations(text: str) -> int:
    """Backticked tokens containing a path separator.

    The same count the retrieval side reads: a page's citations are what an
    answer can quote, so a content change that trades them away is a
    regression no prose metric would catch.
    """
    import re

    return len(re.findall(r"`[^`\n]*/[^`\n]*`", text))


def _pkg(name: str, language: str = "python") -> PackageInfo:
    return PackageInfo(
        name=name,
        path=f"packages/{name}",
        language=language,
        entry_points=[],
        manifest_file="pyproject.toml" if language == "python" else "package.json",
    )


def _parsed(path: str, language: str):
    """A ParsedFile carrying only what the table reads."""
    from repowise.core.ingestion.models import ParsedFile

    return ParsedFile(
        file_info=_make_file_info(path, language=language),
        symbols=[],
        imports=[],
        exports=[],
    )


@pytest.fixture
def structure():
    return RepoStructure(
        is_monorepo=True,
        packages=[_pkg("core"), _pkg("ui", "typescript"), _pkg("cli")],
        root_language_distribution={"python": 0.6, "typescript": 0.4},
        total_files=6,
        total_loc=600,
        entry_points=[],
    )


@pytest.fixture
def parsed_files():
    return [
        _parsed("packages/core/a.py", "python"),
        _parsed("packages/core/b.py", "python"),
        _parsed("packages/core/c.pyi", "python"),
        _parsed("packages/ui/x.tsx", "typescript"),
        _parsed("packages/ui/y.ts", "typescript"),
        _parsed("packages/cli/m.py", "python"),
    ]


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def test_context_counts_files_per_package(sample_config, structure, parsed_files):
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    by_name = {p["name"]: p for p in ctx.package_stats}
    assert by_name["core"]["files"] == 3
    assert by_name["ui"]["files"] == 2
    assert by_name["cli"]["files"] == 1


def test_context_ranks_packages_by_size(sample_config, structure, parsed_files):
    """The table is a reading order, so the biggest package leads."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    assert [p["name"] for p in ctx.package_stats] == ["core", "ui", "cli"]


def test_context_languages_are_observed_not_declared(sample_config, structure, parsed_files):
    """PackageInfo.language is one tag chosen at detection time. The column
    reports what is actually in the directory."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    by_name = {p["name"]: p for p in ctx.package_stats}
    assert by_name["ui"]["languages"] == ["typescript"]
    assert by_name["core"]["languages"] == ["python"]


def test_context_package_with_no_parsed_files_still_appears(sample_config, structure):
    """A package the walker skipped is still a package. Dropping the row would
    silently shorten the table."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=[])
    assert [p["name"] for p in ctx.package_stats] == ["cli", "core", "ui"]
    assert all(p["files"] == 0 for p in ctx.package_stats)


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------


def test_table_has_the_expected_headers(sample_config, structure, parsed_files):
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    table = build_package_table(ctx.package_stats)
    assert "| Package | Path | Files | Languages |" in table
    assert "| `packages/core` |" in table
    assert len(table.splitlines()) == 1 + 1 + 3  # header, separator rule, three rows


def test_table_cites_every_package_path(sample_config, structure, parsed_files):
    """Path citations are what an answer quotes. The table may not cost the
    page any of the ones the old bullet list carried."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    table = build_package_table(ctx.package_stats)
    for pkg in structure.packages:
        assert f"`{pkg.path}`" in table


def test_single_package_repo_renders_a_one_row_table(sample_config):
    """Not an empty table and not a missing section."""
    one = RepoStructure(
        is_monorepo=False,
        packages=[_pkg("only")],
        root_language_distribution={"python": 1.0},
        total_files=1,
        total_loc=10,
        entry_points=[],
    )
    stats = [{"name": "only", "path": "packages/only", "files": 1, "languages": ["python"]}]
    table = build_package_table(stats)
    assert table is not None
    assert len(table.splitlines()) == 3
    assert "| only | `packages/only` | 1 | python |" in table
    del one


def test_no_packages_builds_no_table():
    """A single-package repository has nothing to tabulate. Say nothing rather
    than print a header with no rows under it."""
    assert build_package_table([]) is None


# ---------------------------------------------------------------------------
# embedding
# ---------------------------------------------------------------------------


def test_embed_appends_under_its_own_heading():
    out = embed_package_table("# Page\n\nSome prose.\n", "| Package |\n|---|\n| a |")
    assert "## Packages" in out
    assert "| Package |" in out


def test_embed_is_idempotent():
    """Reused and cached pages get embedded again on every update."""
    once = embed_package_table("# Page\n", "| Package |\n|---|\n| a |")
    twice = embed_package_table(once, "| Package |\n|---|\n| a |")
    assert once == twice


def test_embed_replaces_a_stale_table():
    """The count changes as the repository does; the page must not accumulate
    one table per update."""
    first = embed_package_table("# Page\n", "| Package |\n|---|\n| a |")
    second = embed_package_table(first, "| Package |\n|---|\n| b |")
    assert "| b |" in second
    assert "| a |" not in second
    assert second.count("## Packages") == 1


def test_embed_replaces_the_models_own_packages_section():
    """The model writes a Packages section unprompted. Left alone the reader
    gets the same list twice, once sampled and once counted."""
    page = "# Page\n\n## Packages\n\n- **core** (`packages/core`, python)\n\n## Next\n\ntext\n"
    out = embed_package_table(page, "| Package |\n|---|\n| a |")
    assert out.count("## Packages") == 1
    assert "- **core**" not in out
    assert "## Next" in out
    assert "text" in out


def test_embed_stops_at_the_provenance_footer():
    """The footer is last on the page and carries no heading, so the
    horizontal rule is the only terminator between it and a trailing section.

    It rendered as ``---*Built from...`` for as long as the rule and the text
    were joined, which is not a rule at all, so the section ran to the end of
    the document and took the footer with it.
    """
    page = (
        "# Page\n\n## Packages\n\n- **core** (`packages/core`, python)\n\n"
        "---\n\n*Built from the code's structure.*\n"
    )
    out = embed_package_table(page, "| Package |\n|---|\n| a |")
    assert "Built from the code's structure" in out
    assert "- **core**" not in out


def test_the_stub_footer_renders_a_standalone_rule():
    """Guards the shape the terminator above depends on, at the source."""
    from jinja2 import Environment, PackageLoader

    env = Environment(loader=PackageLoader("repowise.core.generation", "templates"))
    rendered = env.get_template("stub/_footer.j2").render()

    assert rendered.startswith("---\n\n")
    assert "---*" not in rendered


def test_embed_without_a_table_leaves_the_page_alone():
    page = "# Page\n\nprose\n"
    assert embed_package_table(page, None) == page


# ---------------------------------------------------------------------------
# rendered output, both templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", ["repo_overview.j2", "stub/repo_overview.j2"])
def test_language_distribution_table_is_gone(sample_config, structure, parsed_files, template):
    """Step one: a percentage breakdown of the repository's languages told the
    reader nothing they could act on, and the package table carries languages
    per package, which they can."""
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(MockProvider(), assembler, sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    out = gen._render(template, ctx=ctx, repo_git_summary=None)

    assert "Language Distribution" not in out
    assert "## Languages" not in out
    assert "60.0%" not in out


def test_deterministic_page_carries_the_table(sample_config, structure, parsed_files):
    """The structure-only reader has no prose paragraph to carry this, so the
    table is the whole of what they learn about the package layout."""
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(MockProvider(), assembler, sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    page = gen._stub_repo_overview(ctx, "testrepo", "Repository Overview: testrepo", None)
    out = embed_package_table(page.content, build_package_table(ctx.package_stats))

    assert "| Package | Path | Files | Languages |" in out
    assert "`packages/core`" in out


def test_prompt_asks_for_a_role_clause_per_package(sample_config, structure, parsed_files):
    """The role is the one thing on this page no template can derive: it is a
    judgement about what a directory is for. The model is asked for it in
    prose, and the table stays a table of facts."""
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    assembler = ContextAssembler(sample_config)
    gen = PageGenerator(MockProvider(), assembler, sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    prompt = gen._render("repo_overview.j2", ctx=ctx, repo_git_summary=None)

    assert "role" in prompt.lower()
    # and it must tell the model the table is already there, or it writes a
    # second one and the reader gets the packages twice.
    assert "already" in prompt.lower()


async def test_generator_embeds_the_table_on_the_deterministic_page(structure, parsed_files):
    """Through `generate_repo_overview`, not the helpers. The helpers were
    right before this was wired up and the page still had no table."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    page = await gen.generate_repo_overview(
        structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
    )

    assert "| Package | Path | Files | Languages |" in page.content
    assert "| core | `packages/core` | 3 | python |" in page.content
    # and the bullet list it replaced is gone, not sitting above it
    assert "- **core** (`packages/core`" not in page.content
    assert page.content.count("## Packages") == 1


async def test_generator_embed_is_stable_across_two_runs(structure, parsed_files):
    """The point of building this outside the model: same input, same bytes."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    first = await gen.generate_repo_overview(
        structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
    )
    second = await gen.generate_repo_overview(
        structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
    )
    assert first.content == second.content


async def test_deterministic_page_cites_no_fewer_paths_than_before(structure, parsed_files):
    """The floor this change is most likely to breach.

    Replacing a bullet list of paths with a table is a trade of citations for
    layout, and a page that reads better while citing less is a worse answer
    source: a backticked path is what an answer quotes. Counted here rather
    than argued about — the table must carry at least the paths the list did.
    """
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    ctx = gen._assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    before = gen._stub_repo_overview(ctx, "testrepo", "Repository Overview: testrepo", None)
    after = await gen.generate_repo_overview(
        structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
    )

    assert _count_path_citations(after.content) >= _count_path_citations(before.content)
    for pkg in structure.packages:
        assert f"`{pkg.path}`" in after.content


def test_table_build_is_logged(sample_config, structure, parsed_files):
    """Every code path here reports. A table that silently stops appearing is
    the failure this page has already shipped once."""
    assembler = ContextAssembler(sample_config)
    ctx = assembler.assemble_repo_overview(structure, {}, [], {}, parsed_files=parsed_files)
    with capture_logs() as logs:
        build_package_table(ctx.package_stats)
        build_package_table([])
    events = {e["event"] for e in logs}
    assert "overview_package_table_empty" in events


def test_the_provenance_footer_survives_a_table_that_lands_last():
    """The section ends at the footer's rule, not only at the next heading.

    The deterministic page used to render a path table below its packages, so
    the replacement always had a heading to stop at. With those tables gone the
    packages can be the last section, and without this the footer is inside the
    span that gets replaced.
    """
    page = (
        "# Repository Overview: r\n\n## Packages\n- **core** (`packages/core`, python)\n\n"
        "---\n\n*Built from the code's structure.*\n"
    )
    out = embed_package_table(page, "| Package |\n|---|\n| core |")
    assert "| core |" in out
    assert "*Built from the code's structure.*" in out
