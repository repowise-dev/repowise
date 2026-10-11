"""Authoritative full generation retires pages outside its current selection."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from repowise.core.persistence import create_engine, create_session_factory, get_session, init_db
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import tombstone_pages_outside_generation


async def test_full_generation_tombstones_unselected_pages(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}")
    await init_db(engine)
    session_factory = create_session_factory(engine)
    async with get_session(session_factory) as session:
        repo = await upsert_repository(session, name="demo", local_path="/demo")
        for page_id, page_type, target_path, freshness in (
            ("file_page:kept.py", "file_page", "kept.py", "fresh"),
            ("file_page:old.py", "file_page", "old.py", "stale"),
            ("module_page:old", "module_page", "old", "stale"),
            ("scc_page:old", "scc_page", "old", "stale"),
        ):
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type=page_type,
                title=page_id,
                content="# page\n",
                summary="",
                target_path=target_path,
                source_hash="hash",
                model_name="mock",
                provider_name="mock",
                freshness_status=freshness,
            )

        retired = await tombstone_pages_outside_generation(
            session,
            repo.id,
            {"file_page:kept.py"},
        )

    assert set(retired) == {
        "file_page:old.py",
        "module_page:old",
        "scc_page:old",
    }
    async with get_session(session_factory) as session:
        rows = (
            await session.execute(select(Page).where(Page.repository_id == repo.id))
        ).scalars()
        statuses = {page.id: page.freshness_status for page in rows}

    assert statuses == {
        "file_page:kept.py": "fresh",
        "file_page:old.py": "tombstone",
        "module_page:old": "tombstone",
        "scc_page:old": "tombstone",
    }
    await engine.dispose()
