"""Retired template pages cannot keep generate in deterministic mode (#3204)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.cli.commands.generate_cmd import command
from repowise.cli.commands.generate_cmd.engine import _page_stats
from repowise.cli.helpers import load_state
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    await init_db(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(sf, repo_path, *pages: tuple[str, str, str, str]) -> str:
    async with sf() as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        for page_id, page_type, provider, freshness in pages:
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type=page_type,
                title=page_id,
                content="# Page",
                target_path=page_id,
                source_hash="hash",
                model_name="mock",
                provider_name=provider,
                freshness_status=freshness,
            )
        await session.commit()
        return repo.id


@pytest.mark.parametrize("page_type", ["onboarding", "module_page", "repo_overview", "architecture_diagram"])
async def test_tombstoned_templates_are_not_remaining_stubs(session_factory, tmp_path, page_type):
    repo_id = await _seed(
        session_factory, tmp_path,
        ("retired", page_type, "template", "tombstone"),
    )
    assert await _page_stats(session_factory, repo_id) == (1, 0, set())


@pytest.mark.parametrize("freshness", ["fresh", "stale"])
async def test_live_templates_and_stale_ids_remain_counted(session_factory, tmp_path, freshness):
    repo_id = await _seed(
        session_factory, tmp_path,
        ("retired", "onboarding", "template", "tombstone"),
        ("live", "module_page", "template", freshness),
        ("structural", "file_page", "template", "stale"),
        ("written", "repo_overview", "mock", "fresh"),
    )
    assert await _page_stats(session_factory, repo_id) == (
        4, 1, {"live"} if freshness == "stale" else set(),
    )


async def test_stub_count_stays_scoped_to_repository(session_factory, tmp_path):
    repo_id = await _seed(
        session_factory, tmp_path,
        ("retired", "onboarding", "template", "tombstone"),
    )
    await _seed(
        session_factory, tmp_path / "other",
        ("other-live", "module_page", "template", "fresh"),
    )
    assert await _page_stats(session_factory, repo_id) == (1, 0, set())


async def test_tombstones_and_model_free_pages_are_both_excluded(session_factory, tmp_path):
    repo_id = await _seed(
        session_factory, tmp_path,
        ("retired", "onboarding", "template", "tombstone"),
        ("glossary", "onboarding", "template", "fresh"),
        ("live", "module_page", "template", "stale"),
    )
    async with session_factory() as session:
        glossary = await session.get(Page, "glossary")
        assert glossary is not None
        glossary.metadata_json = '{"model_free": true}'
        await session.commit()

    assert await _page_stats(session_factory, repo_id) == (3, 1, {"live"})


@pytest.mark.parametrize("has_live_stub", [False, True])
async def test_docs_mode_updates_after_last_live_stub_is_written(
    session_factory, tmp_path, monkeypatch, has_live_stub,
):
    repo_id = await _seed(
        session_factory, tmp_path,
        ("retired", "onboarding", "template", "tombstone"),
        ("live", "module_page", "template" if has_live_stub else "mock", "fresh"),
    )
    total, stubs, _ = await _page_stats(session_factory, repo_id)
    state = {"docs_mode": "deterministic"}
    monkeypatch.setattr(command, "get_head_commit", lambda _: None)
    command._write_state(
        tmp_path, state,
        SimpleNamespace(provider_name="mock", model_name="mock"),
        SimpleNamespace(total_pages=total, remaining_template_pages=stubs),
    )
    assert load_state(tmp_path)["docs_mode"] == ("deterministic" if has_live_stub else "llm")
    assert load_state(tmp_path)["total_pages"] == 2
