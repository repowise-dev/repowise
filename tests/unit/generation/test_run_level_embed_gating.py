"""Embed gating in ``_GenerationRun.run_level``.

Pages whose content was reused verbatim from the prior run (prompt-hash /
content-hash cache hit) already have an identical vector in any store that
persists across runs; re-embedding them re-bills the embedder for every
unchanged page on every update. run_level therefore skips them, but ONLY when
the store persists across runs: the in-memory store starts empty each run and
still needs every page embedded.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _page(page_id: str, *, reused: bool = False) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    page = GeneratedPage(
        page_id=page_id,
        page_type="file_page",
        title=page_id,
        content=f"# {page_id}",
        source_hash="deadbeef",
        model_name="mock-model",
        provider_name="mock",
        input_tokens=0 if reused else 1,
        output_tokens=0 if reused else 1,
        cached_tokens=0,
        generation_level=2,
        target_path=page_id,
        created_at=now,
        updated_at=now,
    )
    if reused:
        page.metadata["reused_from_prior_run"] = True
    return page


class _RecordingStore:
    def __init__(self, *, persists: bool) -> None:
        self.persists_across_runs = persists
        self.batches: list[list[tuple]] = []

    async def embed_batch(self, items):
        self.batches.append(list(items))


def _fake_run(store) -> SimpleNamespace:
    return SimpleNamespace(
        semaphore=asyncio.Semaphore(4),
        job_system=None,
        job_id=None,
        on_page_done=None,
        on_page_ready=None,
        vector_store=store,
        completed_page_summaries={},
    )


def _run_level(store) -> list[str]:
    async def _go():
        async def fresh():
            return _page("fresh.py")

        async def reused():
            return _page("reused.py", reused=True)

        run = _fake_run(store)
        await _GenerationRun.run_level(
            run, [("p1", fresh()), ("p2", reused())], level=2
        )
        return [pid for batch in store.batches for (pid, *_rest) in batch]

    return asyncio.run(_go())


def test_persistent_store_skips_reused_pages() -> None:
    store = _RecordingStore(persists=True)
    embedded = _run_level(store)
    assert "fresh.py" in embedded
    assert "reused.py" not in embedded


def test_ephemeral_store_still_embeds_reused_pages() -> None:
    """In-memory stores are rebuilt every run: reuse must not starve them."""
    store = _RecordingStore(persists=False)
    embedded = _run_level(store)
    assert "fresh.py" in embedded
    assert "reused.py" in embedded
