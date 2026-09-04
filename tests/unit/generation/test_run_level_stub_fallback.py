"""How ``_GenerationRun.run_level`` treats a stub that stood in for a failure.

The page comes back as a ``GeneratedPage`` (issue #1089), so the level runner
can no longer read "a page arrived" as "the page was written". Two things have
to stay true of the substitute: the job still records it as failed, and it
never reaches the vector store, which is the list ``--resume`` reads back as
the work already done.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.generation.models import STUB_FALLBACK_ERROR, GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _page(page_id: str, *, stub_error: str | None = None) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    page = GeneratedPage(
        page_id=page_id,
        page_type="module_page",
        title=page_id,
        content=f"# {page_id}",
        source_hash="deadbeef",
        model_name="mock-model",
        provider_name="template" if stub_error else "mock",
        input_tokens=0 if stub_error else 1,
        output_tokens=0 if stub_error else 1,
        cached_tokens=0,
        generation_level=4,
        target_path=page_id,
        created_at=now,
        updated_at=now,
    )
    if stub_error:
        page.metadata[STUB_FALLBACK_ERROR] = stub_error
    return page


class _RecordingStore:
    persists_across_runs = True

    def __init__(self) -> None:
        self.batches: list[list[tuple]] = []

    async def embed_batch(self, items):
        self.batches.append(list(items))


class _RecordingJobSystem:
    def __init__(self) -> None:
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.levels: list[int] = []
        self.flushes = 0

    def update_level(self, job_id, level):
        self.levels.append(level)

    def flush(self, job_id):
        self.flushes += 1

    def complete_page(self, job_id, page_id):
        self.completed.append(page_id)

    def fail_page(self, job_id, page_id, error):
        self.failed.append((page_id, error))


def _run_level() -> tuple[list[GeneratedPage], _RecordingJobSystem, _RecordingStore]:
    store = _RecordingStore()
    jobs = _RecordingJobSystem()

    async def _go():
        async def written():
            return _page("module_page:ok")

        async def fell_back():
            return _page("module_page:lost", stub_error="upstream 529 overloaded")

        run = SimpleNamespace(
            semaphore=asyncio.Semaphore(4),
            job_system=jobs,
            job_id="job-1",
            on_page_done=None,
            on_page_ready=None,
            vector_store=store,
            completed_page_summaries={},
            timings=None,
        )
        return await _GenerationRun.run_level(
            run,
            [("module_page:ok", written()), ("module_page:lost", fell_back())],
            level=4,
        )

    return asyncio.run(_go()), jobs, store


def test_stub_fallback_is_recorded_as_a_failed_page() -> None:
    """A run that lost half its pages must not report a clean sweep."""
    _pages, jobs, _store = _run_level()

    assert jobs.failed == [("module_page:lost", "upstream 529 overloaded")]


def test_a_finished_level_flushes_its_checkpoint() -> None:
    """Page completions buffer, so the level boundary has to make them durable."""
    _pages, jobs, _store = _run_level()

    assert jobs.flushes == 1


def test_stub_fallback_is_not_recorded_as_completed() -> None:
    """A page cannot be both, and "completed" is the half a reader believes."""
    _pages, jobs, _store = _run_level()

    assert jobs.completed == ["module_page:ok"]


def test_stub_fallback_is_kept_out_of_the_resume_ledger() -> None:
    """``_seed_resume`` seeds "already done" from the vector store.

    Embedding the stub is what would tell the next ``--resume`` there is
    nothing left to write here, stranding the page it exists to repair.
    """
    _pages, _jobs, store = _run_level()

    embedded = [pid for batch in store.batches for (pid, *_rest) in batch]
    assert embedded == ["module_page:ok"]
