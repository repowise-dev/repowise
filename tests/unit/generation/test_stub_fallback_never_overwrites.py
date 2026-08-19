"""A failure stub must never replace a page that already has prose.

Substituting the stub (issue #1089) turned a failed page from "nothing was
written" into "a row was written", which put it on a collision course with two
places built around the old shape: the upsert, which would archive good prose
behind a version nobody knows to look for, and the superseded-page sweep, whose
contract already carves out "a page that failed to generate claims no files".
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from repowise.core.generation.models import STUB_FALLBACK_ERROR
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.crud.pages import upsert_pages_from_generated
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page, PageVersion
from repowise.core.pipeline.persist import sweep_superseded_generated_pages


class _Gen:
    """Duck-typed GeneratedPage, as the upsert helpers read one."""

    def __init__(self, page_id, page_type, content, provider_name, metadata):
        self.page_id = page_id
        self.page_type = page_type
        self.title = page_id
        self.content = content
        self.summary = ""
        self.target_path = page_id.split(":", 1)[1]
        self.source_hash = "hash"
        self.model_name = "mock-model"
        self.provider_name = provider_name
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.generation_level = 4
        self.confidence = 1.0
        self.freshness_status = "fresh"
        self.metadata = metadata
        self.parent_page_id = None
        self.display_order = 0
        self.section_number = None
        self.structural_key = None
        self.created_at = "2026-07-26T00:00:00+00:00"
        self.updated_at = "2026-07-26T00:00:00+00:00"


def _stub(page_id, *, members=None):
    return _Gen(
        page_id,
        "module_page",
        "# stub\n\nrendered from structure",
        "template",
        {STUB_FALLBACK_ERROR: "upstream 529 overloaded", "file_paths": members or []},
    )


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}")
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_stub_does_not_overwrite_existing_prose(session_factory):
    """One 529 during `update` must not cost the user a written page."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API\n\nreal model-written prose",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
        )

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/api")], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/api"))
        ).scalar_one()
        assert page.content == "# API\n\nreal model-written prose"
        assert page.provider_name == "anthropic"


async def test_stub_over_existing_prose_creates_no_version_churn(session_factory):
    """Not even archived: a snapshot is the kind of loss nobody goes looking for."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API\n\nreal model-written prose",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
        )

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/api")], repo.id)

    async with get_session(session_factory) as session:
        versions = (await session.execute(select(PageVersion))).scalars().all()
        assert versions == []


async def test_keeping_the_prose_still_lets_the_page_move(session_factory):
    """Placement is decided after generation and lands on this write.

    Keeping the prose must not also freeze the page where it used to sit, or a
    failed page quietly falls out of the tree the reader navigates.
    """
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API\n\nreal model-written prose",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
            parent_page_id=None,
            display_order=0,
        )

    moved = _stub("module_page:src/api")
    moved.parent_page_id = "layer_page:services"
    moved.display_order = 7
    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [moved], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/api"))
        ).scalar_one()
        assert page.content == "# API\n\nreal model-written prose"
        assert page.parent_page_id == "layer_page:services"
        assert page.display_order == 7


async def test_prose_page_is_not_recorded_as_a_stub(session_factory):
    """It has prose. Marking it failed would make the stub counters lie."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API\n\nreal model-written prose",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
        )

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/api")], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/api"))
        ).scalar_one()
        assert STUB_FALLBACK_ERROR not in json.loads(page.metadata_json)


async def test_a_stub_landing_on_a_stub_keeps_the_post_generation_enrichment(
    session_factory,
):
    """The double write a normal init does: incremental flush, then final persist.

    No prose is at risk here, so this must take the ordinary idempotent path
    and not the carve-out. The enrichment added between the two writes
    (interlinking, related pages, the tour) reaches the row only through
    ``metadata_json``, which the carve-out deliberately does not write — so
    that is the field that tells the two paths apart.
    """
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/api")], repo.id)

    enriched = _stub("module_page:src/api")
    enriched.metadata["wiki_links"] = ["module_page:src/db"]
    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [enriched], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/api"))
        ).scalar_one()
        metadata = json.loads(page.metadata_json)
        assert metadata["wiki_links"] == ["module_page:src/db"]
        # Still a stub, so `generate` still offers to write it.
        assert page.provider_name == "template"
        assert STUB_FALLBACK_ERROR in metadata


async def test_stub_is_still_written_when_the_page_is_new(session_factory):
    """The whole point: a page nothing ever wrote gets a row to be found by."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/new")], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/new"))
        ).scalar_one()
        assert page.provider_name == "template"


async def test_stub_members_do_not_retire_the_page_whose_generation_failed(
    session_factory,
):
    """A concept group renaming itself while the provider is down.

    The old row's files all appear in the stub written under the new id, so
    without the carve-out the sweep reads the old page as superseded and
    deletes it, taking its version history with it.
    """
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API\n\nreal model-written prose",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
            metadata={"file_paths": ["src/api/a.py", "src/api/b.py"]},
        )

    async with get_session(session_factory) as session:
        swept = await sweep_superseded_generated_pages(
            session,
            repo.id,
            [_stub("module_page:src/api/v2", members=["src/api/a.py", "src/api/b.py"])],
        )

    assert swept == []
    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/api"))
        ).scalar_one_or_none()
        assert page is not None


async def test_a_written_page_still_retires_the_row_it_supersedes(session_factory):
    """The carve-out must not disarm the sweep for pages that did generate."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        await upsert_page(
            session,
            page_id="module_page:src/api",
            repository_id=repo.id,
            page_type="module_page",
            title="API",
            content="# API",
            summary="",
            target_path="src/api",
            source_hash="prompt-hash",
            model_name="mock-model",
            provider_name="anthropic",
            metadata={"file_paths": ["src/api/a.py", "src/api/b.py"]},
        )

    written = _Gen(
        "module_page:src/api/v2",
        "module_page",
        "# API v2\n\nprose",
        "anthropic",
        {"file_paths": ["src/api/a.py", "src/api/b.py"]},
    )
    async with get_session(session_factory) as session:
        swept = await sweep_superseded_generated_pages(session, repo.id, [written])

    assert swept == ["module_page:src/api"]


async def test_the_error_survives_to_the_database(session_factory):
    """A run that produced stubs must be diagnosable after the fact."""
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")

    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, [_stub("module_page:src/new")], repo.id)

    async with get_session(session_factory) as session:
        page = (
            await session.execute(select(Page).where(Page.id == "module_page:src/new"))
        ).scalar_one()
        assert "529" in json.loads(page.metadata_json)[STUB_FALLBACK_ERROR]
