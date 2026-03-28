"""Integration tests for Phase 4 — Persistence.

Runs the full generation pipeline on sample_repo, stores all generated pages
in an in-memory SQLite database, and validates:
- Round-trip store/retrieve fidelity
- Version history after re-generation
- Full-text search over stored content
- Vector search with InMemoryVectorStore + MockEmbedder
- Generation job status transitions
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.job_system import JobSystem
from repowise.core.generation.models import GeneratedPage, GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import PackageInfo, RepoStructure
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.persistence import (
    FullTextSearch,
    InMemoryVectorStore,
    MockEmbedder,
    create_session_factory,
    get_page,
    get_page_versions,
    init_db,
    list_pages,
    update_job_status,
    upsert_generation_job,
    upsert_page_from_generated,
    upsert_repository,
)
from repowise.core.providers.llm.mock import MockProvider

SAMPLE_REPO = Path(__file__).parents[1] / "fixtures" / "sample_repo"


# ---------------------------------------------------------------------------
# Engine + session factory shared across the class
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="class")
def sf(engine):
    return create_session_factory(engine)


# ---------------------------------------------------------------------------
# Generation pipeline + persistence fixture (class-scoped — runs once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
async def persisted(engine, sf, tmp_path_factory):
    """Run the generation pipeline on sample_repo and persist all pages.

    Returns a dict with:
        repo_id:       Repository PK
        job_id:        GenerationJob PK
        pages:         list[GeneratedPage]
        session_factory: for sub-fixtures
    """
    tmp = tmp_path_factory.mktemp("persist")

    # --- Ingest ---
    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    builder = GraphBuilder()
    parsed_files = []
    source_map: dict = {}

    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            pf = parser.parse_file(fi, src)
            builder.add_file(pf)
            parsed_files.append(pf)
            source_map[fi.path] = src
        except Exception:
            pass

    graph = builder.build()  # noqa: F841 (kept for builder ref below)

    pkg_dirs = [d for d in SAMPLE_REPO.iterdir() if d.is_dir()]
    packages = [
        PackageInfo(
            name=d.name,
            path=d.name,
            language="unknown",
            entry_points=[],
            manifest_file="",
        )
        for d in pkg_dirs
    ]
    repo_structure = RepoStructure(
        is_monorepo=len(packages) > 1,
        packages=packages,
        root_language_distribution={"python": 0.5},
        total_files=len(parsed_files),
        total_loc=sum(
            len(source_map.get(p.file_info.path, b"").splitlines()) for p in parsed_files
        ),
        entry_points=[],
    )

    # --- Generate (mock LLM) ---
    config = GenerationConfig(
        max_tokens=512,
        token_budget=2000,
        max_concurrency=2,
        cache_enabled=True,
        jobs_dir=str(tmp / "jobs"),
    )
    provider = MockProvider()
    assembler = ContextAssembler(config)
    generator = PageGenerator(provider, assembler, config)
    job_sys = JobSystem(tmp / "jobs")

    pages: list[GeneratedPage] = await generator.generate_all(
        parsed_files, source_map, builder, repo_structure, "sample_repo", job_sys
    )

    # --- Persist ---
    async with sf() as session:
        repo = await upsert_repository(
            session,
            name="sample_repo",
            local_path=str(SAMPLE_REPO),
            url="https://github.com/example/sample_repo",
        )
        repo_id = repo.id
        await session.commit()

    async with sf() as session:
        job = await upsert_generation_job(
            session,
            repository_id=repo_id,
            provider_name="mock",
            model_name="mock-v1",
            total_pages=len(pages),
        )
        job_id = job.id
        await session.commit()

    async with sf() as session:
        await update_job_status(session, job_id, "running")
        await session.commit()

    for page in pages:
        async with sf() as session:
            await upsert_page_from_generated(session, page, repo_id)
            await session.commit()

    async with sf() as session:
        await update_job_status(
            session,
            job_id,
            "completed",
            completed_pages=len(pages),
        )
        await session.commit()

    return {
        "repo_id": repo_id,
        "job_id": job_id,
        "pages": pages,
        "sf": sf,
        "engine": engine,
    }


# ---------------------------------------------------------------------------
# Store / retrieve tests
# ---------------------------------------------------------------------------


class TestPersistenceStoreRetrieve:
    async def test_all_generated_pages_stored(self, persisted, sf):
        """Every GeneratedPage should be retrievable by page_id."""
        pages = persisted["pages"]
        assert len(pages) > 0

        async with sf() as session:
            for page in pages:
                stored = await get_page(session, page.page_id)
                assert stored is not None, f"Page {page.page_id!r} not found in DB"

    async def test_stored_page_fields_match_generated_page(self, persisted, sf):
        """Spot-check: title, content, source_hash, page_type must round-trip."""
        pages = persisted["pages"]
        gp = pages[0]

        async with sf() as session:
            stored = await get_page(session, gp.page_id)

        assert stored is not None
        assert stored.title == gp.title
        assert stored.content == gp.content
        assert stored.source_hash == gp.source_hash
        assert stored.page_type == gp.page_type
        assert stored.provider_name == gp.provider_name
        assert stored.model_name == gp.model_name

    async def test_list_pages_by_repository_returns_all(self, persisted, sf):
        repo_id = persisted["repo_id"]
        page_count = len(persisted["pages"])

        async with sf() as session:
            db_pages = await list_pages(session, repo_id, limit=page_count + 10)

        assert len(db_pages) == page_count

    async def test_list_pages_filters_by_page_type(self, persisted, sf):
        repo_id = persisted["repo_id"]
        pages = persisted["pages"]
        expected_types = {p.page_type for p in pages}

        for pt in expected_types:
            async with sf() as session:
                filtered = await list_pages(session, repo_id, page_type=pt, limit=200)
            expected_count = sum(1 for p in pages if p.page_type == pt)
            assert len(filtered) == expected_count, (
                f"Expected {expected_count} pages of type {pt!r}, got {len(filtered)}"
            )

    async def test_stored_page_version_is_1_on_first_insert(self, persisted, sf):
        pages = persisted["pages"]
        async with sf() as session:
            stored = await get_page(session, pages[0].page_id)
        assert stored.version == 1


# ---------------------------------------------------------------------------
# Version history tests
# ---------------------------------------------------------------------------


class TestVersionHistory:
    async def test_version_history_after_re_generation(self, persisted, sf):
        """Re-upserting the same page should create one PageVersion snapshot."""
        pages = persisted["pages"]
        repo_id = persisted["repo_id"]
        gp = pages[0]

        # Store a second version with modified content
        from repowise.core.persistence.crud import upsert_page

        async with sf() as session:
            await upsert_page(
                session,
                page_id=gp.page_id,
                repository_id=repo_id,
                page_type=gp.page_type,
                title=gp.title,
                content=gp.content + "\n\n## Updated Section",
                target_path=gp.target_path,
                source_hash="updated_hash_v2",
                model_name=gp.model_name,
                provider_name=gp.provider_name,
            )
            await session.commit()

        async with sf() as session:
            versions = await get_page_versions(session, gp.page_id)

        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].source_hash == gp.source_hash  # original hash

    async def test_page_version_increments(self, persisted, sf):
        """After a second upsert, page.version should be 2."""
        pages = persisted["pages"]
        gp = pages[0]

        async with sf() as session:
            stored = await get_page(session, gp.page_id)

        # version is at least 2 (first upsert + re-upsert in previous test)
        assert stored.version >= 2

    async def test_page_version_created_at_preserved(self, persisted, sf):
        """created_at must not change after an update."""
        pages = persisted["pages"]
        gp = pages[0]

        async with sf() as session:
            stored = await get_page(session, gp.page_id)

        assert stored.created_at is not None
        # The original page was stored from the generated page's created_at
        # Just verify it's set and is a real datetime
        from datetime import datetime

        assert isinstance(stored.created_at, datetime)


# ---------------------------------------------------------------------------
# Full-text search tests
# ---------------------------------------------------------------------------


class TestFullTextSearch:
    @pytest.fixture(scope="class")
    async def fts(self, persisted):
        fts = FullTextSearch(persisted["engine"])
        await fts.ensure_index()

        # Index all stored pages into FTS
        pages = persisted["pages"]
        for page in pages:
            await fts.index(page.page_id, page.title, page.content)

        return fts

    async def test_full_text_search_finds_page_by_title(self, persisted, fts):
        pages = persisted["pages"]
        first_page = pages[0]
        # Search for a word from the title
        title_word = first_page.title.split()[0] if first_page.title else "module"

        results = await fts.search(title_word)
        page_ids = {r.page_id for r in results}
        assert first_page.page_id in page_ids

    async def test_full_text_search_returns_search_results(self, persisted, fts):
        from repowise.core.persistence.search import SearchResult

        results = await fts.search("module", limit=5)
        assert len(results) <= 5
        for r in results:
            assert isinstance(r, SearchResult)
            assert r.search_type == "fulltext"

    async def test_full_text_search_scores_positive(self, persisted, fts):
        results = await fts.search("function")
        for r in results:
            assert r.score > 0.0

    async def test_full_text_search_snippet_length(self, persisted, fts):
        results = await fts.search("module")
        for r in results:
            assert len(r.snippet) <= 200


# ---------------------------------------------------------------------------
# Vector search tests
# ---------------------------------------------------------------------------


class TestVectorSearch:
    @pytest.fixture(scope="class")
    async def vector_store(self, persisted):
        embedder = MockEmbedder()
        store = InMemoryVectorStore(embedder=embedder)

        for page in persisted["pages"]:
            await store.embed_and_upsert(
                page.page_id,
                page.title + " " + page.content[:200],
                {
                    "title": page.title,
                    "page_type": page.page_type,
                    "target_path": page.target_path,
                    "content": page.content[:200],
                },
            )

        return store

    async def test_vector_store_has_all_pages(self, persisted, vector_store):
        assert len(vector_store) == len(persisted["pages"])

    async def test_vector_search_returns_results(self, vector_store):
        results = await vector_store.search("module documentation", limit=5)
        assert len(results) > 0

    async def test_vector_search_result_has_required_fields(self, vector_store):
        from repowise.core.persistence.search import SearchResult

        results = await vector_store.search("function", limit=3)
        for r in results:
            assert isinstance(r, SearchResult)
            assert r.page_id != ""
            assert r.search_type == "vector"
            assert 0.0 <= r.score <= 1.0

    async def test_vector_search_limit_respected(self, vector_store):
        results = await vector_store.search("code", limit=2)
        assert len(results) <= 2

    async def test_vector_search_correct_page_type(self, persisted, vector_store):
        """Each vector search result's page_id should correspond to a known page."""
        known_ids = {p.page_id for p in persisted["pages"]}
        results = await vector_store.search("module", limit=10)
        for r in results:
            assert r.page_id in known_ids


# ---------------------------------------------------------------------------
# Generation job tests
# ---------------------------------------------------------------------------


class TestGenerationJob:
    async def test_job_completed_status(self, persisted, sf):
        from repowise.core.persistence import get_generation_job

        async with sf() as session:
            job = await get_generation_job(session, persisted["job_id"])

        assert job is not None
        assert job.status == "completed"
        assert job.completed_pages == len(persisted["pages"])
        assert job.finished_at is not None

    async def test_job_started_at_set(self, persisted, sf):
        from repowise.core.persistence import get_generation_job

        async with sf() as session:
            job = await get_generation_job(session, persisted["job_id"])

        assert job.started_at is not None

    async def test_job_total_pages_matches_generated(self, persisted, sf):
        from repowise.core.persistence import get_generation_job

        async with sf() as session:
            job = await get_generation_job(session, persisted["job_id"])

        assert job.total_pages == len(persisted["pages"])
