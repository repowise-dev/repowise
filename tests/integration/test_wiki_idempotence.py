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

    Full authority, which is stricter than what ``orchestrator`` would grant
    this fixture: it makes the sweep as aggressive as it can be, so an
    unstable id has the best possible chance of showing up as a stranded row.
    Deliberately not a copy of the orchestrator's rule, which depends on
    curated-KG state this fixture does not have.
    """
    return set(_SWEPT_GENERATED_PAGE_TYPES)


async def _persist_full_run(sf, repo_id: str, pages: list[GeneratedPage]) -> list[str]:
    """The page half of a full index: upsert, sweep, then rebuild the tree.

    Deliberately the same three steps in the same order as
    ``persist_pipeline_result``. The rebuild is part of it: leave it out and
    deleting the production call stays green, which is how it was missed.
    """
    from repowise.core.persistence import upsert_pages_from_generated
    from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

    async with sf() as session:
        await upsert_pages_from_generated(session, pages, repo_id)
        swept = await _sweep_stale_generated_pages(session, repo_id, pages, _authority(pages))
        await rebuild_page_tree(session, repo_id)
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

    async def test_member_keyed_pages_key_on_members_not_on_their_target(self, two_indexes):
        """The point of the column.

        A module's target_path is a clustering ordinal unless a curated
        knowledge graph named a directory, and that ordinal is exactly what
        moves between runs. A structural key equal to the target would record
        the unstable value and be a copy of the page id besides.
        """
        from repowise.core.generation.models import member_structural_key

        checked = 0
        for page in two_indexes["second_pages"]:
            if page.page_type not in ("module_page", "scc_page"):
                continue
            members = page.metadata.get("file_paths") or []
            assert members, f"{page.page_id} recorded no members"
            # module_page rows are minted by the concept tree, which keys them
            # on the "concept" prefix (STRUCTURAL_KEY_PREFIX); scc pages keep
            # their own prefix.
            prefix = "concept" if page.page_type == "module_page" else "scc"
            assert page.structural_key == member_structural_key(members, prefix=prefix)
            checked += 1
        assert checked, "sample repo produced no member-keyed pages"

    async def test_a_module_key_is_not_just_its_target_path(self, two_indexes):
        """Guards the above against being satisfied by a copy."""
        modules = [p for p in two_indexes["second_pages"] if p.page_type == "module_page"]
        assert modules
        assert all(p.structural_key != p.target_path for p in modules)

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


class TestTreeOnRealOutput:
    """The synthetic tree tests cover the shape rules. These check the tree
    that a real generation run actually produces, and that it survives a
    second index unchanged."""

    async def test_the_tree_has_real_depth(self, two_indexes):
        """A flat wiki where every page hangs off the overview satisfies
        "has a parent" and is exactly the failure this must catch.

        The bar is one rung below the top, not two: this repo is indexed
        without a curated knowledge graph, so it has no layer pages and its
        modules sit directly under the overview. Files under modules is the
        deepest this fixture can go, and it is the rung that was broken.
        """
        depths = [
            p.section_number.count(".")
            for p in two_indexes["second_pages"]
            if p.section_number
        ]
        assert depths, "generation produced no tree"
        assert max(depths) >= 1, f"tree is only {max(depths) + 1} levels deep"

    async def test_most_file_pages_sit_under_a_module(self, two_indexes):
        """Modules here are keyed by a clustering ordinal, not a directory, so
        this only passes if placement uses the recorded member list rather
        than a path prefix. It was zero before members were recorded."""
        files = [p for p in two_indexes["second_pages"] if p.page_type == "file_page"]
        under_module = [
            p for p in files if (p.parent_page_id or "").startswith("module_page:")
        ]
        assert len(under_module) >= len(files) // 3, (
            f"only {len(under_module)} of {len(files)} file pages found a module"
        )

    async def test_module_pages_have_children(self, two_indexes):
        modules = {
            p.page_id for p in two_indexes["second_pages"] if p.page_type == "module_page"
        }
        assert modules
        parented = {p.parent_page_id for p in two_indexes["second_pages"]}
        assert modules & parented, "no page sits under any module"

    async def test_no_page_points_at_a_parent_that_does_not_exist(self, two_indexes):
        """A dangling parent breaks every walk of the tree."""
        known = {pid for pid, _, _ in two_indexes["second_rows"]}
        sf = two_indexes["sf"]
        async with sf() as session:
            rows = await session.execute(
                select(Page.id, Page.parent_page_id).where(
                    Page.repository_id == two_indexes["repo_id"],
                    Page.parent_page_id.is_not(None),
                )
            )
            dangling = [(pid, parent) for pid, parent in rows.all() if parent not in known]
        assert dangling == []

    async def test_the_tree_has_no_cycles(self, two_indexes):
        sf = two_indexes["sf"]
        async with sf() as session:
            rows = await session.execute(
                select(Page.id, Page.parent_page_id).where(
                    Page.repository_id == two_indexes["repo_id"]
                )
            )
            parent = dict(rows.all())
        for start in parent:
            seen, node = set(), start
            while node is not None:
                assert node not in seen, f"cycle through {start}"
                seen.add(node)
                node = parent.get(node)

    async def test_the_tree_is_identical_after_the_second_index(self, two_indexes):
        """Placement is derived from structure, so an unchanged repo must
        place its pages exactly where the previous run did."""

        def placement(pages):
            return {
                p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in pages
            }

        assert placement(two_indexes["second_pages"]) == placement(two_indexes["first_pages"])

    async def test_placement_reached_the_database(self, two_indexes):
        """The tree is only useful if it is stored, not just computed."""
        expected = {
            p.page_id: (p.parent_page_id, p.display_order, p.section_number)
            for p in two_indexes["second_pages"]
        }
        sf = two_indexes["sf"]
        async with sf() as session:
            rows = await session.execute(
                select(
                    Page.id, Page.parent_page_id, Page.display_order, Page.section_number
                ).where(Page.repository_id == two_indexes["repo_id"])
            )
            stored = {pid: (parent, order, section) for pid, parent, order, section in rows.all()}
        assert stored == expected
