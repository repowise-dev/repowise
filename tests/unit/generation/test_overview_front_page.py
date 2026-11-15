"""The front page's shape: what it is written from, and what it is told to write.

It is the first page a newcomer opens, so it is written from what that reader
needs (the README in cited sections, the commands, the walkthroughs, the parts
with their own descriptions) and never from counts of files, cycles or
hotspots.

`repo_overview.j2` is a prompt, not a page, so the tests split accordingly:
what the model is *told* is asserted on the rendered prompt, and what ships is
asserted on the page the generator returns.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.onboarding.surface import Journey, Surfaces
from repowise.core.generation.overview_tables import (
    CAPABILITY_TABLE_HEADING,
    PACKAGE_TABLE_HEADING,
    select_capabilities,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.front_page import Part, overview_material
from repowise.core.ingestion.models import PackageInfo, ParsedFile, RepoStructure
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info

README = b"""# Demo

Demo indexes a repository so engineers can ask it questions.

## Quickstart

Run `demo init` in your repository.
"""


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
                definition_source="docs/dead_code.md",
                source_paths=("README.md",),
                doc_frequency=1,
                code_frequency=5,
            )
        ],
        ["Dead Code and Reachability Analysis"],
    )


def _material(capabilities=(), journeys=()):
    return overview_material(
        {"README.md": README},
        surfaces=Surfaces(),
        journeys=list(journeys),
        parts=[Part("Core Overview", "packages/core", 2, "The engine.")],
        edges=[],
        terms=list(capabilities),
    )


def prompt(structure, *, pagerank=None, material=None) -> str:
    config = GenerationConfig()
    assembler = ContextAssembler(config)
    gen = PageGenerator(MockProvider(), assembler, config)
    ctx = assembler.assemble_repo_overview(structure, pagerank or {}, [], {}, material=material)
    return gen._render("repo_overview.j2", ctx=ctx)


class TestWhatItIsWrittenFrom:
    def test_each_readme_section_carries_its_own_span(self, structure):
        body = prompt(structure, material=_material())
        assert "From `README.md:1-4`:" in body
        assert "From `README.md:5-7`:" in body

    def test_parts_carry_their_own_description(self, structure):
        assert "**Core Overview** (`packages/core`, 2 files): The engine." in prompt(
            structure, material=_material()
        )

    def test_defined_terms_reach_the_model(self, structure, capabilities):
        body = prompt(structure, material=_material(capabilities))
        assert "Dead code is code no import path reaches." in body
        assert "## Words you will meet" in body

    def test_walkthroughs_reach_the_model(self, structure):
        j = Journey("How demo init works", "It builds the index.", "1. Parse files.")
        body = prompt(structure, material=_material(journeys=[j]))
        assert "#### How demo init works" in body
        assert "Walkthrough: **<page title>**" in body

    def test_no_walkthroughs_means_no_walkthrough_section(self, structure):
        assert "## What happens when it runs`:" not in prompt(structure, material=_material())


class TestWhatItIsToldToWrite:
    def test_the_shape_is_fixed(self, structure):
        body = prompt(structure, material=_material())
        order = ["## Using it", "## How the parts fit", "## The parts", "## New here?"]
        assert [body.index(h) for h in order] == sorted(body.index(h) for h in order)

    def test_counts_are_not_reported(self, structure):
        body = prompt(structure, pagerank={"packages/core/a.py": 0.9}, material=_material())
        assert "Do not report counts of files, imports, hotspots, cycles or clusters." in body
        assert "PageRank" not in body and "Hotspots" not in body

    def test_states_its_length_once(self, structure):
        body = prompt(structure, material=_material())
        assert "about 700 to 900 words" in body
        assert "backticked repository-relative paths" in body


class TestPathsAreEvidenceNotContent:
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


async def test_the_model_page_carries_no_capability_or_package_table(
    structure, parsed_files, capabilities
) -> None:
    """The written page has its own parts table and defined terms; the
    structural tables stay on the keyless page."""
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
    assert CAPABILITY_TABLE_HEADING not in page.content
    assert PACKAGE_TABLE_HEADING not in page.content


async def test_model_overview_repairs_machine_local_file_links(structure, parsed_files) -> None:
    provider = MockProvider(
        responses=[
            GeneratedResponse(
                content=(
                    "## Project Summary\n\nRead [the core]"
                    "(C:\\worktrees\\rw-upper\\packages\\core\\a.py)."
                ),
                input_tokens=10,
                output_tokens=10,
            )
        ]
    )
    config = GenerationConfig()
    gen = PageGenerator(provider, ContextAssembler(config), config)

    page = await gen.generate_repo_overview(
        structure,
        {},
        [],
        {},
        repo_name="testrepo",
        parsed_files=parsed_files,
    )

    assert "C:\\worktrees" not in page.content
    assert "rw-upper" not in page.content
    assert "the core (`packages/core/a.py`)" in page.content


class TestNoReadmeIsOnlySaidWhenTrue:
    def test_a_repository_without_a_readme_says_so(self, structure):
        material = overview_material(
            {}, surfaces=Surfaces(), journeys=[], parts=[], edges=[], terms=[]
        )
        assert "The repository has no README." in prompt(structure, material=material)

    def test_missing_material_never_claims_an_absence(self, structure):
        assert "no README" not in prompt(structure, material=None)

    async def test_a_structural_only_overview_makes_no_model_call(self, structure, parsed_files):
        config = GenerationConfig()
        provider = MockProvider()
        gen = PageGenerator(provider, ContextAssembler(config), config)
        page = await gen.generate_repo_overview(
            structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files,
            structural_only=True,
        )
        assert provider.call_count == 0
        assert "no README" not in page.content
        assert page.provider_name == "template"
