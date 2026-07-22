"""Per-page re-render mode on an index-only update.

A page a user upgraded to model prose must not silently revert to a template on
the next `repowise update`. `partition_regenerate_by_provenance` is the split
that keeps that promise: it reads each changed file's existing page provenance
and routes template pages to the free template render and model-written pages to
the model render.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.cli.commands.update_cmd.deterministic import (
    partition_regenerate_by_provenance,
)
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_pages_from_generated,
    upsert_repository,
)


def _page(path: str, provider_name: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=f"file_page:{path}",
        page_type="file_page",
        title=path,
        content="body",
        source_hash=f"hash-{path}",
        model_name="template" if provider_name == "template" else "gpt-x",
        provider_name=provider_name,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=2,
        target_path=path,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repo_dir(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    db = tmp_path / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    return tmp_path


def _seed(repo_dir, pages: list[GeneratedPage]) -> None:
    # Seed synchronously (asyncio.run, no running loop): the function under test
    # is called from sync CLI code and drives its own loop via ``run_async``, so
    # the whole test must stay off the event loop or that call raises.
    import asyncio
    import os

    async def _do() -> None:
        engine = create_engine(os.environ["REPOWISE_DB_URL"])
        try:
            await init_db(engine)
            sf = create_session_factory(engine)
            async with get_session(sf) as session:
                repo = await upsert_repository(
                    session, name=repo_dir.name, local_path=str(repo_dir)
                )
                await upsert_pages_from_generated(session, pages, repo.id)
        finally:
            await engine.dispose()

    asyncio.run(_do())


def test_splits_template_from_model(repo_dir):
    _seed(
        repo_dir,
        [
            _page("a.py", "template"),
            _page("b.py", "openai"),
            _page("c.py", "template"),
        ],
    )
    template, model = partition_regenerate_by_provenance(
        repo_dir, ["a.py", "b.py", "c.py"]
    )
    assert template == ["a.py", "c.py"]
    assert model == ["b.py"]


def test_unknown_path_is_a_template(repo_dir):
    # A newly added file has no page yet — it renders as a template (the free,
    # safe default), never as a model page we can't afford.
    _seed(repo_dir, [_page("a.py", "openai")])
    template, model = partition_regenerate_by_provenance(repo_dir, ["a.py", "new.py"])
    assert template == ["new.py"]
    assert model == ["a.py"]


def test_preserves_input_order(repo_dir):
    _seed(repo_dir, [_page("x.py", "openai"), _page("y.py", "template")])
    template, model = partition_regenerate_by_provenance(repo_dir, ["y.py", "x.py"])
    assert template == ["y.py"]
    assert model == ["x.py"]


def test_empty_input_is_two_empty_lists(repo_dir):
    assert partition_regenerate_by_provenance(repo_dir, []) == ([], [])


def test_lookup_failure_keeps_pages_never_reverts(tmp_path, monkeypatch):
    # When provenance can't be read, every path is treated as model-written so
    # the free template render can never overwrite (revert) a page we couldn't
    # classify. The caller then re-renders with a provider or keeps + marks stale.
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{(tmp_path / 'missing.db').as_posix()}"
    )

    def _boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.deterministic._load_model_written_paths",
        _boom,
    )
    template, model = partition_regenerate_by_provenance(tmp_path, ["a.py", "b.py"])
    assert template == []
    assert model == ["a.py", "b.py"]
