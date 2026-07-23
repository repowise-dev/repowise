"""The metadata handed to the vector store at generation time.

``title`` is in the coverage-rerank haystack and the grounding corpus on the
serving side, so a blank one is a silent ranking tax rather than a cosmetic
bug. It was blank for every page embedded during generation until 2026-07,
while ``reindex`` and ``doctor --repair`` set it correctly, meaning the same
page scored differently depending on which code path last wrote it.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _embed_item
from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder


def _page(**overrides) -> GeneratedPage:
    fields = {
        "page_id": "file_page:pkg/mod.py",
        "page_type": "file_page",
        "title": "mod.py (ingestion)",
        "content": "# mod.py\n\n## Overview\n\nParses a module into symbols.\n",
        "source_hash": "abc123",
        "model_name": "template",
        "provider_name": "template",
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "generation_level": 1,
        "target_path": "pkg/mod.py",
        "created_at": "2026-07-23T00:00:00Z",
        "updated_at": "2026-07-23T00:00:00Z",
    }
    fields.update(overrides)
    return GeneratedPage(**fields)  # type: ignore[arg-type]


def test_embed_metadata_carries_the_page_title():
    _pid, _text, meta = _embed_item(_page())
    assert meta["title"] == "mod.py (ingestion)"


def test_embed_metadata_title_is_never_silently_blank():
    """A page always has a title; the metadata must not drop it."""
    _pid, _text, meta = _embed_item(_page(title="Retrieval Pipeline"))
    assert meta.get("title"), "blank title in embed metadata is the 2026-07 bug"


@pytest.mark.asyncio
async def test_title_survives_into_the_vector_store():
    """End-to-end through the store the generation path actually writes to."""
    store = InMemoryVectorStore(MockEmbedder())
    await store.embed_batch([_embed_item(_page())])

    results = await store.search("parses a module", limit=1)
    assert results
    assert results[0].title == "mod.py (ingestion)"
