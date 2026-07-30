"""The per-input embedding cap reports what it drops.

``EMBED_TEXT_MAX_CHARS`` silently truncates any text past it. That is the
right thing to do — the embedder rejects an oversized input outright, and one
page must not sink the whole batch it travels in — but until now the drop
left no trace at all. A page whose tail is missing from its vector is
indistinguishable, at every later stage, from a page that never contained
those words: search does not find it, and nothing reports a fault.

Assertions go through ``structlog.testing.capture_logs`` rather than
``caplog``: structlog's sink is reconfigured by other suites in this repo, so
records do not reliably reach the stdlib handler caplog reads.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from repowise.core.persistence.vector_store._base import (
    EMBED_TEXT_MAX_CHARS,
    iter_embed_chunks,
)


def _drain(items: list[tuple[str, str, dict]]) -> list[str]:
    """Consume the generator and return every capped text it yielded."""
    out: list[str] = []
    for _chunk, capped in iter_embed_chunks(items):
        out.extend(capped)
    return out


def _truncations(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e.get("event") == "embed_text_truncated"]


def test_an_over_cap_text_reports_the_page_and_the_characters_dropped():
    over_by = 1_234
    text = "x" * (EMBED_TEXT_MAX_CHARS + over_by)

    with capture_logs() as logs:
        capped = _drain([("file_page:a/b.py", text, {})])

    assert capped == ["x" * EMBED_TEXT_MAX_CHARS]

    events = _truncations(logs)
    assert len(events) == 1
    assert events[0]["log_level"] == "warning"
    assert events[0]["page_id"] == "file_page:a/b.py"
    assert events[0]["chars"] == EMBED_TEXT_MAX_CHARS + over_by
    assert events[0]["chars_dropped"] == over_by
    assert events[0]["cap"] == EMBED_TEXT_MAX_CHARS


def test_an_under_cap_text_reports_nothing():
    with capture_logs() as logs:
        capped = _drain([("file_page:a/b.py", "short body", {})])

    assert capped == ["short body"]
    assert _truncations(logs) == []


def test_a_text_exactly_at_the_cap_reports_nothing():
    """The cap is the largest size that survives whole, not the first casualty."""
    text = "x" * EMBED_TEXT_MAX_CHARS

    with capture_logs() as logs:
        capped = _drain([("file_page:a/b.py", text, {})])

    assert capped == [text]
    assert _truncations(logs) == []


def test_each_over_cap_page_is_named_separately():
    """A run reports every page it truncated, not that truncation happened.

    The count of affected pages is what sizes the fix; a single "some pages
    were truncated" line cannot be acted on.
    """
    items = [
        ("file_page:one.py", "x" * (EMBED_TEXT_MAX_CHARS + 10), {}),
        ("file_page:two.py", "small", {}),
        ("file_page:three.py", "x" * (EMBED_TEXT_MAX_CHARS + 20), {}),
    ]

    with capture_logs() as logs:
        _drain(items)

    named = [e["page_id"] for e in _truncations(logs)]
    assert named == ["file_page:one.py", "file_page:three.py"]


def test_pages_are_named_across_chunk_boundaries():
    """Truncation is per item, and the batching must not hide a later chunk.

    ``iter_embed_chunks`` slices into batches; an over-cap page in the second
    slice is exactly as invisible as one in the first, and is reported the
    same way.
    """
    items: list[tuple[str, str, dict]] = [(f"file_page:{i}.py", "small", {}) for i in range(20)]
    items[19] = ("file_page:last.py", "x" * (EMBED_TEXT_MAX_CHARS + 5), {})

    with capture_logs() as logs:
        _drain(items)

    named = [e["page_id"] for e in _truncations(logs)]
    assert named == ["file_page:last.py"]


@pytest.mark.asyncio
async def test_embed_texts_reports_truncation_too():
    """The loose-string path shares the cap, so it shares the report.

    It embeds text belonging to no page, so ``page_id`` is empty — an honest
    blank rather than a fabricated id.
    """
    from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    store = InMemoryVectorStore(MockEmbedder())

    with capture_logs() as logs:
        vectors = await store.embed_texts(["x" * (EMBED_TEXT_MAX_CHARS + 7)])

    assert vectors is not None and len(vectors) == 1
    events = _truncations(logs)
    assert len(events) == 1
    assert events[0]["page_id"] == ""
    assert events[0]["chars_dropped"] == 7
