"""The subject reuse key survives the persistence round-trip (issue #1089).

``content_hash`` is only useful if it is actually written and read back:
``load_prior_pages`` feeds the reuse gate, so a key dropped on write would
silently degrade every later run back to the source-hash gate that re-bills
everything.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    load_prior_pages,
    upsert_page,
    upsert_repository,
)
from repowise.core.persistence.crud import upsert_pages_from_generated
from repowise.core.persistence.models import Page


def _generated(page_id: str, *, content_hash: str, provider: str = "mock") -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=page_id,
        page_type="module_page",
        title=f"Title {page_id}",
        content="# Body",
        source_hash="source-hash",
        content_hash=content_hash,
        model_name="mock-model",
        provider_name=provider,
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
        generation_level=4,
        target_path="pkg/resolvers",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
async def repo_dir(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    db = tmp_path / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    return tmp_path


async def test_content_hash_round_trips_through_upsert_and_load(repo_dir):
    """Write via ``upsert_page`` (the single-page path), read via
    ``load_prior_pages`` — the exact pair the generator + resume wrapper use."""
    import os

    engine = create_engine(os.environ["REPOWISE_DB_URL"])
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_dir.name, local_path=str(repo_dir))
            repo_id = repo.id
            await upsert_page(
                session,
                page_id="module_page:pkg/resolvers",
                repository_id=repo_id,
                page_type="module_page",
                title="Resolvers",
                content="# Resolvers\n\nprose",
                summary="",
                target_path="pkg/resolvers",
                source_hash="prompt-hash",
                content_hash="subject-key-123",
                model_name="mock-model",
                provider_name="mock",
            )

        async with get_session(sf) as session:
            prior = await load_prior_pages(session, repo_id)

        entry = prior["module_page:pkg/resolvers"]
        assert entry.content_hash == "subject-key-123"
        assert entry.provider_name == "mock"  # the stub-refusal guard's input
        assert entry.model_name == "mock-model"
        assert entry.content == "# Resolvers\n\nprose"
    finally:
        await engine.dispose()


async def test_content_hash_round_trips_through_batch_upsert(repo_dir):
    """The batch path (``upsert_pages_from_generated``) — what the full
    persistence sweep and ``generate`` both use — keeps the key too."""
    import os

    engine = create_engine(os.environ["REPOWISE_DB_URL"])
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_dir.name, local_path=str(repo_dir))
            repo_id = repo.id
            await upsert_pages_from_generated(
                session, [_generated("module_page:pkg", content_hash="key-batch")], repo_id
            )

        async with get_session(sf) as session:
            row = (
                await session.execute(select(Page).where(Page.id == "module_page:pkg"))
            ).scalar_one()
            assert row.content_hash == "key-batch"

        async with get_session(sf) as session:
            prior = await load_prior_pages(session, repo_id)
        assert prior["module_page:pkg"].content_hash == "key-batch"
        assert prior["module_page:pkg"].provider_name == "mock"
    finally:
        await engine.dispose()


async def test_legacy_row_without_key_loads_with_empty_content_hash(repo_dir):
    """A store written before this column existed has no key: the gate falls
    back to the prompt hash instead of exploding."""
    import os


    engine = create_engine(os.environ["REPOWISE_DB_URL"])
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_dir.name, local_path=str(repo_dir))
            # Simulate the pre-migration row: the ORM writes the default, and
            # the schema reconciler back-filled the column with ''.
            await upsert_page(
                session,
                page_id="module_page:old",
                repository_id=repo.id,
                page_type="module_page",
                title="Old",
                content="old prose",
                summary="",
                target_path="old",
                source_hash="h",
                model_name="m",
                provider_name="mock",
            )

        async with get_session(sf) as session:
            prior = await load_prior_pages(session, repo.id)
        assert prior["module_page:old"].content_hash == ""
    finally:
        await engine.dispose()
