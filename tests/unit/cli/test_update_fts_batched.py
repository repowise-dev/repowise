"""The update's page persist writes the full-text index in one batch.

Every FTS write deletes by ``page_id``, which the index stores unindexed, so a
per-page loop rescans the whole index once per page. On a large wiki that was
the dominant cost of a full re-render, so the write must be a single call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.cli.commands.update_cmd.deterministic import persist_deterministic_pages
from repowise.core.generation.models import GeneratedPage
from repowise.core.persistence.search import FullTextSearch


def _page(i: int) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=f"file_page:m{i}.py",
        page_type="file_page",
        title=f"File: m{i}.py",
        content=f"Module {i} documents widget number {i}.",
        source_hash=f"hash-{i}",
        model_name="template",
        provider_name="template",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=2,
        target_path=f"m{i}.py",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def repo_dir(tmp_path, monkeypatch):
    (tmp_path / ".repowise").mkdir()
    db = tmp_path / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    return tmp_path


def test_persist_indexes_every_page_in_one_batch(repo_dir, monkeypatch):
    calls: list[int] = []
    original = FullTextSearch.index_many

    async def _spy(self, pages):
        calls.append(len(pages))
        await original(self, pages)

    monkeypatch.setattr(FullTextSearch, "index_many", _spy)

    degraded: list[str] = []
    total = persist_deterministic_pages(
        repo_path=repo_dir,
        generated_pages=[_page(i) for i in range(25)],
        decay_paths=[],
        degraded=degraded,
    )

    assert degraded == []
    assert total == 25
    assert calls == [25]
