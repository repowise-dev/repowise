"""Integration tests for fully deterministic (no-LLM) generation.

``repowise init --index-only`` renders every page type from a Jinja template
instead of calling a model. These tests run the real generation pipeline over
the sample_repo fixture with ``GenerationConfig.deterministic=True`` and a
provider that raises on any call, so a page type that forgets to branch fails
loudly rather than silently costing tokens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import PackageInfo, ParsedFile, RepoStructure
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.providers.llm.template import TemplateProvider

SAMPLE_REPO = Path(__file__).parents[1] / "fixtures" / "sample_repo"


@pytest.fixture(scope="module")
async def deterministic_pages(tmp_path_factory):
    """Run generate_all() in deterministic mode and return the page list."""
    tmp = tmp_path_factory.mktemp("det_gen")

    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    builder = GraphBuilder()
    parsed_files: list[ParsedFile] = []
    source_map: dict[str, bytes] = {}

    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, src)
            builder.add_file(parsed)
            parsed_files.append(parsed)
            source_map[fi.path] = src
        except Exception:
            pass

    builder.build()

    packages = [
        PackageInfo(name=d.name, path=d.name, language="unknown", entry_points=[], manifest_file="")
        for d in SAMPLE_REPO.iterdir()
        if d.is_dir()
    ]
    repo_structure = RepoStructure(
        is_monorepo=len(packages) > 1,
        packages=packages,
        root_language_distribution={"python": 0.7, "typescript": 0.3},
        total_files=len(parsed_files),
        total_loc=sum(
            len(source_map.get(p.file_info.path, b"").splitlines()) for p in parsed_files
        ),
        entry_points=[],
    )

    config = GenerationConfig(
        deterministic=True,
        max_concurrency=3,
        jobs_dir=str(tmp / "jobs"),
    )
    generator = PageGenerator(TemplateProvider(), ContextAssembler(config), config)

    return await generator.generate_all(
        parsed_files,
        source_map,
        builder,
        repo_structure,
        "sample_repo",
        job_system=None,
    )


class TestDeterministicGeneration:
    def test_pages_generated(self, deterministic_pages):
        assert deterministic_pages, "deterministic run produced no pages"

    def test_no_provider_calls(self, deterministic_pages):
        """TemplateProvider.generate() raises, so reaching here proves no page
        type fell through to the LLM path."""
        assert all(p.provider_name == "template" for p in deterministic_pages)

    def test_zero_token_cost(self, deterministic_pages):
        for p in deterministic_pages:
            assert p.input_tokens == 0
            assert p.output_tokens == 0
            assert p.cached_tokens == 0

    def test_every_page_marked_deterministic(self, deterministic_pages):
        for p in deterministic_pages:
            assert p.metadata.get("deterministic") is True, p.page_id

    def test_every_page_has_content(self, deterministic_pages):
        for p in deterministic_pages:
            assert p.content.strip(), f"empty content for {p.page_id}"
            assert p.summary, f"empty summary for {p.page_id}"

    def test_no_duplicate_page_ids(self, deterministic_pages):
        ids = [p.page_id for p in deterministic_pages]
        assert len(ids) == len(set(ids))

    def test_every_page_carries_the_provenance_footer(self, deterministic_pages):
        """The footer is what tells a reader (and the UI) the page is
        structural rather than written, so no page may skip it."""
        for p in deterministic_pages:
            assert "Generated deterministically" in p.content, p.page_id

    def test_covers_multiple_page_types(self, deterministic_pages):
        """Deterministic mode is only worth shipping if it produces a whole
        wiki, not just file pages."""
        types = {p.page_type for p in deterministic_pages}
        assert "file_page" in types
        assert "repo_overview" in types
        assert "architecture_diagram" in types
        assert len(types) >= 4, f"only got {types}"

    def test_full_file_coverage(self, deterministic_pages):
        """The budget is bypassed, so every parsed code file should have a
        page, which is the whole point of the mode."""
        file_pages = [p for p in deterministic_pages if p.page_type == "file_page"]
        assert len(file_pages) >= 5
        # No page may land in the budget-dropped coverage tail: nothing was
        # dropped, so nothing should be tagged as tail (doc_tier 3).
        assert all(p.metadata.get("doc_tier") != 3 for p in file_pages)

    def test_content_hash_left_empty(self, deterministic_pages):
        """The cross-run reuse gate keys on model_name, not provider_name. A
        stamped content_hash here would let a later LLM run serve template
        content as if a model had written it."""
        for p in deterministic_pages:
            assert not p.content_hash, p.page_id

    def test_pages_are_not_prompts(self, deterministic_pages):
        """A template rendered through the prompt path would carry
        instructions to the model. Catch that leaking into page content."""
        for p in deterministic_pages:
            assert "Generate a complete wiki page" not in p.content, p.page_id
            assert not p.content.lstrip().startswith("You are "), p.page_id


class TestFilePagesOnly:
    """The incremental path: only the changed files' own pages.

    An update holds only the files that changed. Levels 3 and up describe the
    whole repository and read ``parsed_files``, so letting them run against
    that slice rewrites a whole-repo page from a one-commit view: a codebase
    map with no directories, a module page claiming one file. The flag stops
    them, leaving those pages as the last full run wrote them.
    """

    @pytest.fixture(scope="class")
    async def scoped_pages(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("det_scoped")
        traverser = FileTraverser(SAMPLE_REPO)
        parser = ASTParser()
        builder = GraphBuilder()
        parsed_files: list[ParsedFile] = []
        source_map: dict[str, bytes] = {}
        for fi in traverser.traverse():
            try:
                src = Path(fi.abs_path).read_bytes()
                parsed = parser.parse_file(fi, src)
                builder.add_file(parsed)
                parsed_files.append(parsed)
                source_map[fi.path] = src
            except Exception:
                pass
        builder.build()
        repo_structure = RepoStructure(
            is_monorepo=False,
            packages=[],
            root_language_distribution={"python": 1.0},
            total_files=len(parsed_files),
            total_loc=0,
            entry_points=[],
        )
        # One code file, the way an incremental run arrives. It has to be one
        # the selector would pick: file pages are only built for code.
        one = [p for p in parsed_files if p.file_info.language == "python"][:1]
        one_src = {p.file_info.path: source_map[p.file_info.path] for p in one}
        config = GenerationConfig(
            deterministic=True,
            file_pages_only=True,
            max_concurrency=2,
            jobs_dir=str(tmp / "jobs"),
        )
        generator = PageGenerator(TemplateProvider(), ContextAssembler(config), config)
        return await generator.generate_all(
            one, one_src, builder, repo_structure, "sample_repo", job_system=None
        )

    def test_no_repo_wide_pages(self, scoped_pages):
        repo_wide = {
            "scc_page",
            "module_page",
            "layer_page",
            "repo_overview",
            "architecture_diagram",
            "onboarding",
        }
        produced = {p.page_type for p in scoped_pages}
        assert not (produced & repo_wide), f"repo-wide pages rebuilt from one file: {produced}"

    def test_still_produces_the_file_page(self, scoped_pages):
        assert scoped_pages, "the changed file's own page must still be rendered"
        assert all(p.provider_name == "template" for p in scoped_pages)


class TestOnlyPageIds:
    """Scoped generation: emit an arbitrary subset from the complete view.

    Unlike ``file_pages_only`` (which stops the level ladder), ``only_page_ids``
    runs every level over the whole parsed repo and emits only the requested
    ids. That is what lets ``repowise generate --page`` refresh one repo-wide
    page correctly rather than from a truncated slice.
    """

    @pytest.fixture(scope="class")
    async def full_ids(self, tmp_path_factory):
        """Every page id a full deterministic run over the fixture produces."""
        return {p.page_id for p in await _run_deterministic(tmp_path_factory, "only_full")}

    async def test_emits_exactly_the_requested_subset(self, tmp_path_factory, full_ids):
        # Ask for one file page plus the repo overview — a mix of a leaf page
        # and a level-6 whole-repo page.
        a_file = next(pid for pid in full_ids if pid.startswith("file_page:"))
        overview = next(pid for pid in full_ids if pid.startswith("repo_overview:"))
        requested = {a_file, overview}
        pages = await _run_deterministic(
            tmp_path_factory, "only_subset", only_page_ids=requested
        )
        produced = {p.page_id for p in pages}
        assert produced == requested, f"scoped run leaked or dropped pages: {produced ^ requested}"

    async def test_unrequested_repo_wide_page_is_absent(self, tmp_path_factory, full_ids):
        a_file = next(pid for pid in full_ids if pid.startswith("file_page:"))
        pages = await _run_deterministic(tmp_path_factory, "only_onefile", only_page_ids={a_file})
        assert {p.page_id for p in pages} == {a_file}


async def _run_deterministic(tmp_path_factory, name, *, only_page_ids=None):
    """Run a full deterministic generate_all over the fixture repo."""
    tmp = tmp_path_factory.mktemp(name)
    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    builder = GraphBuilder()
    parsed_files: list[ParsedFile] = []
    source_map: dict[str, bytes] = {}
    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, src)
            builder.add_file(parsed)
            parsed_files.append(parsed)
            source_map[fi.path] = src
        except Exception:
            pass
    builder.build()
    repo_structure = RepoStructure(
        is_monorepo=False,
        packages=[],
        root_language_distribution={"python": 1.0},
        total_files=len(parsed_files),
        total_loc=0,
        entry_points=[],
    )
    config = GenerationConfig(
        deterministic=True,
        max_concurrency=3,
        jobs_dir=str(tmp / "jobs"),
    )
    generator = PageGenerator(TemplateProvider(), ContextAssembler(config), config)
    return await generator.generate_all(
        parsed_files,
        source_map,
        builder,
        repo_structure,
        "sample_repo",
        job_system=None,
        only_page_ids=only_page_ids,
    )
