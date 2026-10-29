"""An update touching a flow page's step file refreshes or decays that page.

Store-backed: the pages, their stored ``file_paths`` and their freshness live in
a real ``wiki.db``, so the query that picks the pages is what is tested.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import stale_targets

# Stored flow pages: id -> (provider, files its steps live in). The second one's
# entry function was renamed away, so only its stored files can still find it.
_STORED = {
    "flow_page:a.py::run": ("template", ["a.py", "b.py"]),
    "flow_page:old.py::gone": ("openai", ["b.py", "c.py"]),
    "flow_page:d.py::serve": ("template", ["d.py"]),
}


def _db_url(repo_path: Path) -> str:
    return f"sqlite+aiosqlite:///{repo_path / '.repowise' / 'wiki.db'}"


async def _seed(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    (repo_path / ".repowise").mkdir(parents=True)
    engine = create_engine(_db_url(repo_path))
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        repo = await upsert_repository(session, name="r", local_path=str(repo_path))
        for page_id, (provider, files) in _STORED.items():
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="flow_page",
                title=page_id,
                content="body",
                target_path=page_id.split(":", 1)[1],
                source_hash="h",
                model_name="m",
                provider_name=provider,
                metadata={"file_paths": files},
            )
        await session.commit()
    await engine.dispose()


async def _freshness(repo_path: Path) -> dict[str, str]:
    from sqlalchemy import select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(_db_url(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(select(Page.id, Page.freshness_status))
            return dict(rows.all())
    finally:
        await engine.dispose()


@pytest.fixture
def run(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    repo_path = (tmp_path / "repo").resolve()
    asyncio.run(_seed(repo_path))
    return SimpleNamespace(
        repo_path=repo_path,
        file_diffs=[SimpleNamespace(path="b.py", old_path=None)],
        stale_deterministic_ids=set(),
        degraded=[],
    )


def test_a_changed_step_file_decays_every_flow_page_walking_through_it(run):
    assert stale_targets.stale_flow_pages(run) == 2
    assert asyncio.run(_freshness(run.repo_path)) == {
        "flow_page:a.py::run": "stale",
        # Its entry no longer exists; the stored files still find it.
        "flow_page:old.py::gone": "stale",
        "flow_page:d.py::serve": "fresh",
    }
    assert run.degraded == []


def test_only_keyless_pages_are_queued_for_a_free_re_render(run):
    stale_targets.stale_flow_pages(run)
    assert run.stale_deterministic_ids == {"flow_page:a.py::run"}


def test_a_rename_matches_on_the_old_path(run):
    run.file_diffs = [SimpleNamespace(path="e.py", old_path="d.py")]
    assert stale_targets.stale_flow_pages(run) == 1
    assert asyncio.run(_freshness(run.repo_path))["flow_page:d.py::serve"] == "stale"
    assert run.stale_deterministic_ids == {"flow_page:d.py::serve"}


def test_a_change_no_flow_walks_through_touches_nothing(run):
    run.file_diffs = [SimpleNamespace(path="z.py", old_path=None)]
    assert stale_targets.stale_flow_pages(run) == 0
    assert set(asyncio.run(_freshness(run.repo_path)).values()) == {"fresh"}
    assert run.stale_deterministic_ids == set()
