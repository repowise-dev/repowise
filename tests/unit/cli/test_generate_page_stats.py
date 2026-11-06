"""``_page_stats`` counts unwritten pages the way ``generate --unwritten`` selects them.

A deterministic onboarding subkind (Active Landscape) is rendered by the template
provider and stamped ``metadata.model_free``. The planner never offers it to
``--unwritten``, so the end-of-run counter must not report it as unwritten
either, or the "N page(s) still unwritten" hint can never clear (#3614).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.cli.commands.generate_cmd.engine import _page_stats
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db


@pytest.fixture
async def session_factory():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


async def _seed(sf, tmp_path, *pages: tuple[str, str, str, dict | None]) -> str:
    async with sf() as session:
        repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
        await session.commit()
        for page_id, page_type, provider, metadata in pages:
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type=page_type,
                title=page_id,
                content="# Page",
                summary="Page",
                target_path=page_id.split(":", 1)[1],
                source_hash="h",
                model_name="mock",
                provider_name=provider,
                metadata=metadata,
            )
        await session.commit()
        return repo.id


async def test_model_free_page_is_not_counted_as_unwritten(session_factory, tmp_path):
    repo_id = await _seed(
        session_factory,
        tmp_path,
        ("onboarding:onboarding/active_landscape", "onboarding", "template", {"model_free": True}),
        ("module_page:pkg", "module_page", "mock", None),
    )

    total, stubs, _ = await _page_stats(session_factory, repo_id)

    assert total == 2
    assert stubs == 0


async def test_template_concept_page_is_still_counted(session_factory, tmp_path):
    repo_id = await _seed(
        session_factory,
        tmp_path,
        ("onboarding:onboarding/active_landscape", "onboarding", "template", {"model_free": True}),
        ("module_page:pkg", "module_page", "template", {"deterministic": True}),
        ("repo_overview:r", "repo_overview", "template", None),
        ("file_page:a.py", "file_page", "template", None),
    )

    total, stubs, _ = await _page_stats(session_factory, repo_id)

    assert total == 4
    # The module page and the overview; the file page is structural.
    assert stubs == 2
