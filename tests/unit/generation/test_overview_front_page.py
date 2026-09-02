"""The front page's shape: what it is told to write, and what it stopped printing.

`/repos/[id]/docs` opens the repo overview, and it read like a directory
listing. Two reasons, both fixed here. The prompt's payload had no
natural-language input at all, so nothing in it said what the product was for;
and it rendered an entry-point table and a top-files-by-PageRank table, which
are lists a reader can produce with `ls`.

`repo_overview.j2` is a prompt, not a page, so the tests split accordingly:
what the model is *told* is asserted on the rendered prompt, and what ships is
asserted on the page the generator returns.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.overview_tables import (
    CAPABILITY_TABLE_HEADING,
    PACKAGE_TABLE_HEADING,
    select_capabilities,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.models import PackageInfo, ParsedFile, RepoStructure
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info

DIGEST = "## The intelligence layers\nFive of them, over one index."


@pytest.fixture
def structure():
    return RepoStructure(
        is_monorepo=True,
        packages=[
            PackageInfo(
                name="core",
                path="packages/core",
                language="python",
                manifest_file=None,
                entry_points=[],
            )
        ],
        root_language_distribution={"python": 1.0},
        total_files=2,
        total_loc=200,
        entry_points=["packages/cli/main.py"],
    )


@pytest.fixture
def parsed_files():
    return [
        ParsedFile(file_info=_make_file_info(p), symbols=[], imports=[], exports=[])
        for p in ("packages/core/a.py", "packages/core/b.py")
    ]


@pytest.fixture
def capabilities():
    return select_capabilities(
        [
            HouseTerm(
                term="Dead code",
                definition="Dead code is code no import path reaches.",
                definition_source="src/analysis/dead_code.py",
                source_paths=("README.md",),
                doc_frequency=1,
                code_frequency=5,
                is_indexed_symbol=False,
            )
        ],
        ["Dead Code and Reachability Analysis"],
    )


def prompt(structure, *, pagerank=None, prose_digest="") -> str:
    config = GenerationConfig()
    assembler = ContextAssembler(config)
    gen = PageGenerator(MockProvider(), assembler, config)
    ctx = assembler.assemble_repo_overview(
        structure, pagerank or {}, [], {}, prose_digest=prose_digest
    )
    return gen._render("repo_overview.j2", ctx=ctx, repo_git_summary=None)


class TestTheProseKeyhole:
    def test_the_digest_reaches_the_model(self, structure):
        assert DIGEST in prompt(structure, prose_digest=DIGEST)

    def test_it_is_scoped_to_vocabulary_and_framing(self, structure):
        """Prose may name things. It may not name paths, counts or packages —
        the guardrail the planner's measured collapse leaves behind."""
        body = prompt(structure, prose_digest=DIGEST)
        opening = body[: body.index("## Structural facts")]
        assert "vocabulary, framing and what the product is for" in opening
        assert "Do **not** take a path, a count, a package name" in opening

    def test_structure_is_still_named_as_the_authority(self, structure):
        assert "Authoritative for every path, count and name." in prompt(structure)

    def test_no_readme_leaves_no_empty_section(self, structure):
        """A repository with a thin or absent README is the common case."""
        assert "What the project says about itself" not in prompt(structure)


class TestWhatItIsToldToWrite:
    def test_the_shape_is_fixed(self, structure):
        body = prompt(structure)
        assert body.index("## Project Summary") < body.index("## Architecture")
        assert body.index("## Architecture") < body.index("## Key concepts")

    def test_key_concepts_are_ideas_not_directories(self, structure):
        assert "Not a package, not a directory, not a file." in prompt(structure)

    def test_enumerable_facts_are_no_longer_pushed_into_lists(self, structure):
        """The old contract said to put facts in a table or a list, which is
        the instruction that produced a page of paths."""
        assert "in a table or a list" not in prompt(structure)


class TestPathsAreEvidenceNotContent:
    def test_pagerank_stays_in_the_payload(self, structure):
        """Ranking evidence: it is how the model knows what to call central."""
        body = prompt(structure, pagerank={"packages/core/a.py": 0.9})
        assert "packages/core/a.py" in body
        assert "PageRank" in body

    def test_and_the_model_is_told_not_to_print_it(self, structure):
        assert "**Do not print any of these paths.**" in prompt(structure)

    def test_the_required_sections_no_longer_include_entry_points(self):
        from repowise.core.generation.page_generator.prompts import SYSTEM_PROMPTS

        assert "## Entry Points" not in SYSTEM_PROMPTS["repo_overview"]

    async def test_the_deterministic_page_prints_no_path_tables(
        self, structure, parsed_files
    ) -> None:
        """The keyless page is a real shipped page, not a fallback stub."""
        config = GenerationConfig(deterministic=True)
        gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
        page = await gen.generate_repo_overview(
            structure,
            {"packages/core/a.py": 0.9},
            [],
            {},
            repo_name="testrepo",
            parsed_files=parsed_files,
        )
        assert "## Entry Points" not in page.content
        assert "## Most Central Files" not in page.content

    async def test_it_still_names_the_entry_point_in_a_sentence(
        self, structure, parsed_files
    ) -> None:
        """Where a path is worth reading, it stays. A table of them is not."""
        config = GenerationConfig(deterministic=True)
        gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
        page = await gen.generate_repo_overview(
            structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
        )
        assert "packages/cli/main.py" in page.content


async def test_what_it_does_lands_above_what_it_is_made_of(
    structure, parsed_files, capabilities
) -> None:
    """Reading order: the capabilities answer the question the packages do not.

    The model path only. Both tables are appended to whatever the model wrote,
    and it is now told to write neither, so the append order is the reading
    order. The deterministic page carries ``## Packages`` in its own template
    and keeps that position.
    """
    config = GenerationConfig()
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    page = await gen.generate_repo_overview(
        structure,
        {},
        [],
        {},
        repo_name="testrepo",
        parsed_files=parsed_files,
        capabilities=capabilities,
    )
    assert page.content.index(CAPABILITY_TABLE_HEADING) < page.content.index(PACKAGE_TABLE_HEADING)
