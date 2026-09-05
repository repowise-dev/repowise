"""What an interrupted run loses from the job checkpoint, and what it cannot.

``complete_page`` buffers, so a kill between a page landing and the next flush
drops that page from ``completed_page_ids``. These tests pin the two halves of
that trade: the window is real, and it cannot cost a resumed run any coverage,
because resume derives its skip set from the vector store rather than from this
file.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.core.generation.job_system import JobSystem
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator.orchestrate import _GenerationRun
from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore


class _StubEmbedder:
    """Fixed-width vectors; these tests never rank, they only count ids."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0] for _ in texts]


def _job(tmp_path) -> tuple[JobSystem, str]:
    js = JobSystem(tmp_path / "jobs")
    job_id = js.create_job(".", GenerationConfig(), "mock", "mock-model-1")
    js.start_job(job_id, 2)
    return js, job_id


def _on_disk(tmp_path, job_id: str) -> dict:
    return json.loads((tmp_path / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_interrupt_between_page_and_flush_costs_no_resume_coverage(tmp_path):
    store = InMemoryVectorStore(_StubEmbedder())
    js, job_id = _job(tmp_path)

    # The durable rows land first — this is the ordering a real level uses.
    await store.embed_batch(
        [("file_page:a.py", "a", {}), ("file_page:b.py", "b", {})]
    )
    js.complete_page(job_id, "file_page:a.py")
    js.complete_page(job_id, "file_page:b.py")

    # The process dies here: after the pages persisted, before any flush.
    del js

    # The window is real - the checkpoint kept neither page.
    assert _on_disk(tmp_path, job_id)["completed_page_ids"] == []

    # A resumed run still covers both, because it asks the store, not the file.
    run = SimpleNamespace(
        job_system=JobSystem(tmp_path / "jobs"),
        resume=True,
        vector_store=store,
        completed_ids=set(),
        preserved_page_ids=set(),
        only_page_ids=None,
    )
    await _GenerationRun._seed_resume(run)

    assert run.completed_ids == {"file_page:a.py", "file_page:b.py"}
    assert _GenerationRun._emit(run, "file_page:a.py") is False
    assert _GenerationRun._emit(run, "file_page:b.py") is False
    # Skipped pages are held out of the sweep, so a resume does not delete them.
    assert run.preserved_page_ids == {"file_page:a.py", "file_page:b.py"}


@pytest.mark.asyncio
async def test_a_page_whose_rows_never_landed_is_regenerated(tmp_path):
    """The other side: no durable row means resume must not skip it."""
    store = InMemoryVectorStore(_StubEmbedder())
    js, job_id = _job(tmp_path)

    await store.embed_batch([("file_page:a.py", "a", {})])
    js.complete_page(job_id, "file_page:a.py")
    # b was generated but its rows never reached the store before the kill.
    js.complete_page(job_id, "file_page:b.py")
    del js

    run = SimpleNamespace(
        job_system=JobSystem(tmp_path / "jobs"),
        resume=True,
        vector_store=store,
        completed_ids=set(),
        preserved_page_ids=set(),
        only_page_ids=None,
    )
    await _GenerationRun._seed_resume(run)

    assert _GenerationRun._emit(run, "file_page:b.py") is True


def test_a_flushed_level_survives_the_same_kill(tmp_path):
    """The bound in reverse: what a level boundary made durable stays durable."""
    js, job_id = _job(tmp_path)
    js.complete_page(job_id, "file_page:a.py")
    js.flush(job_id)
    js.complete_page(job_id, "file_page:b.py")
    del js

    assert _on_disk(tmp_path, job_id)["completed_page_ids"] == ["file_page:a.py"]
