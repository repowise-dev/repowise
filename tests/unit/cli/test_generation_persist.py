"""Unit tests for the resume-friendly generation wrapper.

Covers :func:`run_generation_with_persistence`: pages handed to the
``on_page_ready`` sink are flushed to the database during the run, and a
subsequent run loads them back as ``prior_pages`` for reuse.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.cli.commands._generation_persist import run_generation_with_persistence
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)


def _page(page_id: str, content: str = "body") -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=page_id,
        page_type="file_page",
        title=f"Title {page_id}",
        content=content,
        source_hash=f"hash-{page_id}",
        model_name="model-1",
        provider_name="provider-1",
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
        generation_level=2,
        target_path=f"{page_id}.py",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repo_dir(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    db = tmp_path / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    return tmp_path


async def _read_db_page_ids(repo_dir) -> set[str]:
    import os

    engine = create_engine(os.environ["REPOWISE_DB_URL"])
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_dir.name, local_path=str(repo_dir))
            from repowise.core.persistence.crud import list_pages

            pages = await list_pages(session, repo.id, limit=100)
            return {p.id for p in pages}
    finally:
        await engine.dispose()


async def test_pages_flushed_incrementally(repo_dir, monkeypatch):
    """Every page handed to on_page_ready lands in the DB by the time the
    wrapper returns — even though the caller never persists explicitly."""
    emitted = [_page("alpha"), _page("beta")]

    async def fake_run_generation(*, repo_path, on_page_ready=None, prior_pages=None, **_kw):
        # ``repo_path`` is required (no default) so a wrapper that stops
        # forwarding it fails here instead of silently passing — the gap that
        # let the real ``run_generation``'s required ``repo_path`` go unpassed.
        assert repo_path == repo_dir
        # Mirror the generator: fire the sink the instant each page is ready.
        for p in emitted:
            on_page_ready(p)
        return emitted

    monkeypatch.setattr(
        "repowise.core.pipeline.run_generation", fake_run_generation, raising=True
    )

    pages = await run_generation_with_persistence(
        repo_path=repo_dir,
        repo_name=repo_dir.name,
    )

    assert {p.page_id for p in pages} == {"alpha", "beta"}
    stored = await _read_db_page_ids(repo_dir)
    assert stored == {"alpha", "beta"}


async def test_prior_pages_loaded_on_second_run(repo_dir, monkeypatch):
    """A second run sees the first run's pages as prior_pages for reuse."""
    seen_prior: dict = {}

    async def first_run(*, repo_path, on_page_ready=None, prior_pages=None, **_kw):
        on_page_ready(_page("gamma"))
        return [_page("gamma")]

    async def second_run(*, repo_path, on_page_ready=None, prior_pages=None, **_kw):
        seen_prior.update(prior_pages or {})
        return []

    monkeypatch.setattr("repowise.core.pipeline.run_generation", first_run, raising=True)
    await run_generation_with_persistence(repo_path=repo_dir, repo_name=repo_dir.name)

    monkeypatch.setattr("repowise.core.pipeline.run_generation", second_run, raising=True)
    await run_generation_with_persistence(repo_path=repo_dir, repo_name=repo_dir.name)

    assert "gamma" in seen_prior


async def test_sink_failure_never_breaks_generation(repo_dir, monkeypatch):
    """A page the persister can't store must not abort the run."""

    async def fake_run_generation(*, repo_path, on_page_ready=None, prior_pages=None, **_kw):
        # A bare object lacks GeneratedPage attributes → upsert raises inside
        # the consumer, which must swallow it.
        on_page_ready(object())
        on_page_ready(_page("delta"))
        return [_page("delta")]

    monkeypatch.setattr(
        "repowise.core.pipeline.run_generation", fake_run_generation, raising=True
    )

    pages = await run_generation_with_persistence(repo_path=repo_dir, repo_name=repo_dir.name)
    assert {p.page_id for p in pages} == {"delta"}
    # The valid page still persisted despite the bad one.
    stored = await _read_db_page_ids(repo_dir)
    assert "delta" in stored
