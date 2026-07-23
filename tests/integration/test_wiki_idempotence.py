"""Two consecutive full indexes of an unchanged tree must converge.

``test_generation_determinism.py`` proves the *generator* mints the same page
ids twice. That is necessary and not sufficient: the ids then go through an
upsert keyed on ``page_id`` and a sweep that retires structurally-keyed pages
the run did not reproduce. A page type whose key drifts, or one that is missing
from the sweep list, produces a store that grows on every re-index while the
generator looks perfectly stable.

These tests close that gap at the store level: generate twice, persist both
runs into the same database through the real persist pair, and assert the row
set is byte-identical. That is the only guard against silently doubling the
wiki, and it is the contract any new structurally-keyed page type has to meet
before it ships.

Read ``test_persist_sweep_pages.py`` for the sweep's own unit behaviour. This
file exercises the composition, on real generated output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GeneratedPage, GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import PackageInfo, ParsedFile, RepoStructure
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import (
    _SWEPT_GENERATED_PAGE_TYPES,
    _sweep_stale_generated_pages,
)

SAMPLE_REPO = Path(__file__).parents[1] / "fixtures" / "sample_repo"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _parse_sample_repo() -> tuple[list[ParsedFile], dict[str, bytes]]:
    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    parsed_files: list[ParsedFile] = []
    source_map: dict[str, bytes] = {}
    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed_files.append(parser.parse_file(fi, src))
            source_map[fi.path] = src
        except Exception:
            pass
    return parsed_files, source_map


async def _generate(parsed_files, source_map, tmp) -> list[GeneratedPage]:
    """One full generation run over the sample repo, no LLM."""
    from repowise.core.providers.llm.template import TemplateProvider

    builder = GraphBuilder()
    for p in parsed_files:
        builder.add_file(p)
    builder.build()

    packages = [
        PackageInfo(
            name=d.name, path=d.name, language="unknown", entry_points=[], manifest_file=""
        )
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
    config = GenerationConfig(deterministic=True, max_concurrency=3, jobs_dir=str(tmp / "jobs"))
    generator = PageGenerator(TemplateProvider(), ContextAssembler(config), config)
    return await generator.generate_all(
        parsed_files, source_map, builder, repo_structure, "sample_repo", job_system=None
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _authority(pages: list[GeneratedPage]) -> set[str]:
    """The swept types this run is entitled to speak for.

    A deterministic template run enumerates every candidate rather than a
    budgeted slice, so its output for a swept type is ground truth. Mirrors
    what ``orchestrator`` grants a curated deterministic run.
    """
    return set(_SWEPT_GENERATED_PAGE_TYPES)


async def _persist_full_run(sf, repo_id: str, pages: list[GeneratedPage]) -> list[str]:
    """The page half of a full index: batch upsert, then sweep.

    Deliberately the same pair, in the same order, as
    ``persist_pipeline_result`` — the phases either side of it do not touch
    wiki_pages.
    """
    from repowise.core.persistence import upsert_pages_from_generated

    async with sf() as session:
        await upsert_pages_from_generated(session, pages, repo_id)
        swept = await _sweep_stale_generated_pages(session, repo_id, pages, _authority(pages))
        await session.commit()
    return swept


async def _page_rows(sf, repo_id: str) -> list[tuple[str, str, str]]:
    async with sf() as session:
        rows = await session.execute(
            select(Page.id, Page.page_type, Page.target_path)
            .where(Page.repository_id == repo_id)
            .order_by(Page.id)
        )
        return [tuple(r) for r in rows.all()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def sf(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture(scope="module")
async def two_indexes(sf, tmp_path_factory):
    """Index the same tree twice into one store, snapshotting rows after each."""
    from repowise.core.persistence.crud import upsert_repository

    tmp = tmp_path_factory.mktemp("wiki_idempotence")
    parsed_files, source_map = _parse_sample_repo()

    async with sf() as session:
        repo = await upsert_repository(
            session,
            name="sample_repo",
            local_path=str(SAMPLE_REPO),
            url="https://github.com/example/sample_repo",
        )
        repo_id = repo.id
        await session.commit()

    first_pages = await _generate(parsed_files, source_map, tmp / "a")
    first_swept = await _persist_full_run(sf, repo_id, first_pages)
    first_rows = await _page_rows(sf, repo_id)

    second_pages = await _generate(parsed_files, source_map, tmp / "b")
    second_swept = await _persist_full_run(sf, repo_id, second_pages)
    second_rows = await _page_rows(sf, repo_id)

    return {
        "repo_id": repo_id,
        "first_pages": first_pages,
        "second_pages": second_pages,
        "first_rows": first_rows,
        "second_rows": second_rows,
        "first_swept": first_swept,
        "second_swept": second_swept,
        "sf": sf,
    }


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestReindexIdempotence:
    async def test_the_run_produced_something(self, two_indexes):
        """Guards the rest of the class from passing vacuously on an empty run."""
        assert two_indexes["first_pages"]
        assert two_indexes["first_rows"]

    async def test_page_count_is_unchanged_by_the_second_index(self, two_indexes):
        """The headline contract. A growing count is the wiki doubling."""
        assert len(two_indexes["second_rows"]) == len(two_indexes["first_rows"])

    async def test_page_ids_are_unchanged_by_the_second_index(self, two_indexes):
        """Stronger than the count: the same rows, not merely as many."""
        assert two_indexes["second_rows"] == two_indexes["first_rows"]

    async def test_second_index_sweeps_nothing(self, two_indexes):
        """Nothing to retire means no id moved between the two runs.

        A non-empty sweep here would still leave the counts equal (one row
        retired, one minted), so this is the assertion that localises an
        unstable key rather than just detecting growth.
        """
        assert two_indexes["second_swept"] == []

    async def test_no_duplicate_target_path_within_a_page_type(self, two_indexes):
        """Two ids for one thing is the doubling failure wearing a disguise.

        Distinct page types legitimately share a target_path (a file page and
        its api_contract), so the uniqueness claim is per type.
        """
        seen: dict[tuple[str, str], int] = {}
        for _id, page_type, target in two_indexes["second_rows"]:
            seen[(page_type, target)] = seen.get((page_type, target), 0) + 1
        assert [k for k, n in seen.items() if n > 1] == []

    async def test_every_swept_type_present_is_reproduced_identically(self, two_indexes):
        """Per-type restatement, so a failure names the type that drifted."""

        def by_type(rows):
            out: dict[str, set[str]] = {}
            for _id, page_type, _target in rows:
                out.setdefault(page_type, set()).add(_id)
            return out

        first, second = by_type(two_indexes["first_rows"]), by_type(two_indexes["second_rows"])
        for page_type in _SWEPT_GENERATED_PAGE_TYPES:
            assert first.get(page_type) == second.get(page_type), page_type

    async def test_second_index_does_not_grow_the_version_history(self, two_indexes):
        """An unchanged tree must take the idempotent-touch branch.

        If re-indexing archived a version per page it would mean the content
        hash moved, which is the same instability as a moving id one level
        down, and it grows the store without bound.
        """
        from repowise.core.persistence.models import PageVersion

        sf = two_indexes["sf"]
        async with sf() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(PageVersion)
                .where(PageVersion.repository_id == two_indexes["repo_id"])
            )
        assert total == 0


class TestStructuralKeys:
    """Every page whose identity is structural must say so on the row.

    The three swept page types are already keyed on structure through their
    target_path. Recording that in its own column is what lets identity and
    location stop being the same string: a page can then keep a real directory
    as its target_path, which is what readers and links need, while its
    identity follows its members.
    """

    async def test_swept_types_carry_a_structural_key(self, two_indexes):
        placed = [
            (pid, ptype)
            for pid, ptype, _ in two_indexes["second_rows"]
            if ptype in _SWEPT_GENERATED_PAGE_TYPES
        ]
        assert placed, "sample repo produced no structurally-keyed pages"

        sf = two_indexes["sf"]
        async with sf() as session:
            rows = await session.execute(
                select(Page.id, Page.page_type, Page.target_path, Page.structural_key).where(
                    Page.repository_id == two_indexes["repo_id"],
                    Page.page_type.in_(_SWEPT_GENERATED_PAGE_TYPES),
                )
            )
            for pid, _ptype, target, key in rows.all():
                assert key == target, pid

    async def test_file_pages_have_no_structural_key(self, two_indexes):
        """A file page is identified by its path. Nothing structural to record."""
        sf = two_indexes["sf"]
        async with sf() as session:
            rows = await session.execute(
                select(Page.id, Page.structural_key).where(
                    Page.repository_id == two_indexes["repo_id"],
                    Page.page_type == "file_page",
                )
            )
            assert [pid for pid, key in rows.all() if key is not None] == []

    async def test_structural_keys_are_stable_across_the_two_indexes(self, two_indexes):
        """The whole point: the key must not move when nothing moved."""
        first = {p.page_id: p.structural_key for p in two_indexes["first_pages"]}
        second = {p.page_id: p.structural_key for p in two_indexes["second_pages"]}
        assert first == second
