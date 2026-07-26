"""Unit tests for the resume-friendly generation wrapper.

Covers :func:`run_generation_with_persistence`: pages handed to the
``on_page_ready`` sink are flushed to the database during the run, and a
subsequent run loads them back as ``prior_pages`` for reuse.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.cli.commands.init_cmd._generation_persist import run_generation_with_persistence
from repowise.core.generation import GenerationConfig, JobSystem
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.pipeline.phases.generation import run_generation


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


async def test_generation_completion_callback_reports_only_current_run_failures(
    repo_dir, monkeypatch
):
    """The callback excludes stale and concurrent jobs from this run's result."""
    jobs = JobSystem(repo_dir / ".repowise" / "jobs")
    stale_job = jobs.create_job(str(repo_dir), GenerationConfig(), "test", "test")
    jobs.start_job(stale_job, 1)
    jobs.fail_page(stale_job, "stale-page", "old failure")
    jobs.complete_job(stale_job)

    class ControlledGenerator:
        def __init__(self, *_args, **_kwargs) -> None:
            self.last_job_id: str | None = None

        async def generate_all(self, *_args, job_system, **_kwargs):
            current_job = job_system.create_job(str(repo_dir), GenerationConfig(), "test", "test")
            job_system.start_job(current_job, 1)
            job_system.fail_page(current_job, "current-page", "controlled failure")
            job_system.complete_job(current_job)
            self.last_job_id = current_job

            # Simulate another run creating a checkpoint after this one. A
            # list-diff approach cannot distinguish the two new jobs.
            other_job = job_system.create_job(str(repo_dir), GenerationConfig(), "test", "test")
            job_system.start_job(other_job, 1)
            job_system.fail_page(other_job, "other-page", "other run failure")
            job_system.complete_job(other_job)
            return []

    monkeypatch.setattr("repowise.core.generation.PageGenerator", ControlledGenerator)

    reported_failures: list[list[str]] = []
    pages = await run_generation(
        repo_path=repo_dir,
        parsed_files=[],
        source_map={},
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        llm_client=object(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=None,
        on_generation_complete=reported_failures.append,
    )

    assert pages == []
    assert reported_failures == [["current-page"]]


async def test_generation_ignores_completion_callback_exception(repo_dir, monkeypatch):
    """A completion reporting failure cannot stop otherwise successful generation."""

    class ControlledGenerator:
        def __init__(self, *_args, **_kwargs) -> None:
            self.last_job_id = None

        async def generate_all(self, *_args, job_system, **_kwargs):
            self.last_job_id = job_system.create_job(
                str(repo_dir), GenerationConfig(), "test", "test"
            )
            job_system.start_job(self.last_job_id, 0)
            job_system.complete_job(self.last_job_id)
            return []

    monkeypatch.setattr("repowise.core.generation.PageGenerator", ControlledGenerator)

    callback_called = False

    def failing_callback(_failed_page_ids: list[str]) -> None:
        nonlocal callback_called
        callback_called = True
        raise RuntimeError("reporting unavailable")

    pages = await run_generation(
        repo_path=repo_dir,
        parsed_files=[],
        source_map={},
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        llm_client=object(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=None,
        on_generation_complete=failing_callback,
    )

    assert pages == []
    assert callback_called


async def test_generation_ignores_failed_completion_checkpoint_read(repo_dir, monkeypatch):
    """A failed read of this run's checkpoint cannot stop generation."""

    class ControlledGenerator:
        def __init__(self, *_args, **_kwargs) -> None:
            self.last_job_id = None

        async def generate_all(self, *_args, job_system, **_kwargs):
            self.last_job_id = job_system.create_job(
                str(repo_dir), GenerationConfig(), "test", "test"
            )
            job_system.start_job(self.last_job_id, 0)
            job_system.complete_job(self.last_job_id)
            return []

    def fail_get_checkpoint(self, _job_id):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr("repowise.core.generation.PageGenerator", ControlledGenerator)
    monkeypatch.setattr(JobSystem, "get_checkpoint", fail_get_checkpoint)

    reported_failures: list[list[str]] = []
    pages = await run_generation(
        repo_path=repo_dir,
        parsed_files=[],
        source_map={},
        graph_builder=object(),
        repo_structure=object(),
        git_meta_map={},
        llm_client=object(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=None,
        on_generation_complete=reported_failures.append,
    )

    assert pages == []
    assert reported_failures == []
