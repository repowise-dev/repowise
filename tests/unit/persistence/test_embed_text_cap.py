"""The per-input embedding cap reports what it drops.

``EMBED_TEXT_MAX_CHARS`` silently truncates any text past it. That is the
right thing to do — the embedder rejects an oversized input outright, and one
page must not sink the whole batch it travels in — but until now the drop
left no trace at all. A page whose tail is missing from its vector is
indistinguishable, at every later stage, from a page that never contained
those words: search does not find it, and nothing reports a fault.
"""

from __future__ import annotations

import logging

import pytest

from repowise.core.persistence.vector_store._base import (
    EMBED_TEXT_MAX_CHARS,
    iter_embed_chunks,
)

LOGGER = "repowise.core.persistence.vector_store._base"


def _drain(items: list[tuple[str, str, dict]]) -> list[str]:
    """Consume the generator and return every capped text it yielded."""
    out: list[str] = []
    for _chunk, capped in iter_embed_chunks(items):
        out.extend(capped)
    return out


def _reports(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "embed_text_truncated" in r.getMessage()]


def test_an_over_cap_text_reports_the_page_and_the_characters_dropped(caplog):
    over_by = 1_234
    text = "x" * (EMBED_TEXT_MAX_CHARS + over_by)

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        capped = _drain([("file_page:a/b.py", text, {})])

    assert capped == ["x" * EMBED_TEXT_MAX_CHARS]

    reports = _reports(caplog)
    assert len(reports) == 1
    message = reports[0].getMessage()
    assert "page_id=file_page:a/b.py" in message
    assert f"chars={EMBED_TEXT_MAX_CHARS + over_by}" in message
    assert f"chars_dropped={over_by}" in message
    assert f"cap={EMBED_TEXT_MAX_CHARS}" in message


def test_the_report_survives_the_level_the_cli_runs_at(caplog):
    """It has to outrank the filter, or it exists only in this file.

    ``repowise init`` — the command that writes the index, and so the one
    that does the truncating — pins ``repowise.core`` to ``ERROR`` unless
    ``--verbose`` is passed. A warning here would be discarded exactly where
    it matters.
    """
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        _drain([("file_page:a.py", "x" * (EMBED_TEXT_MAX_CHARS + 1), {})])

    assert [r.levelno for r in _reports(caplog)] == [logging.ERROR]


def test_an_under_cap_text_reports_nothing(caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        capped = _drain([("file_page:a/b.py", "short body", {})])

    assert capped == ["short body"]
    assert _reports(caplog) == []


def test_a_text_exactly_at_the_cap_reports_nothing(caplog):
    """The cap is the largest size that survives whole, not the first casualty."""
    text = "x" * EMBED_TEXT_MAX_CHARS

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        capped = _drain([("file_page:a/b.py", text, {})])

    assert capped == [text]
    assert _reports(caplog) == []


def test_each_over_cap_page_is_named_separately(caplog):
    """A run reports every page it truncated, not that truncation happened.

    The count of affected pages is what sizes the fix; a single "some pages
    were truncated" line cannot be acted on.
    """
    items = [
        ("file_page:one.py", "x" * (EMBED_TEXT_MAX_CHARS + 10), {}),
        ("file_page:two.py", "small", {}),
        ("file_page:three.py", "x" * (EMBED_TEXT_MAX_CHARS + 20), {}),
    ]

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        _drain(items)

    named = [r.getMessage() for r in _reports(caplog)]
    assert len(named) == 2
    assert "page_id=file_page:one.py" in named[0]
    assert "page_id=file_page:three.py" in named[1]


def test_pages_are_named_across_chunk_boundaries(caplog):
    """Truncation is per item, and the batching must not hide a later chunk.

    ``iter_embed_chunks`` slices into batches; an over-cap page in the second
    slice is exactly as invisible as one in the first, and is reported the
    same way.
    """
    items: list[tuple[str, str, dict]] = [(f"file_page:{i}.py", "small", {}) for i in range(20)]
    items[19] = ("file_page:last.py", "x" * (EMBED_TEXT_MAX_CHARS + 5), {})

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        _drain(items)

    named = [r.getMessage() for r in _reports(caplog)]
    assert len(named) == 1
    assert "page_id=file_page:last.py" in named[0]


@pytest.mark.asyncio
async def test_embed_texts_reports_truncation_too(caplog):
    """The loose-string path shares the cap, so it shares the report.

    It embeds text belonging to no page, so ``page_id`` is empty — an honest
    blank rather than a fabricated id.
    """
    from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    store = InMemoryVectorStore(MockEmbedder())

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        vectors = await store.embed_texts(["x" * (EMBED_TEXT_MAX_CHARS + 7)])

    assert vectors is not None and len(vectors) == 1
    reports = _reports(caplog)
    assert len(reports) == 1
    assert "page_id= " in reports[0].getMessage() + " "
    assert "chars_dropped=7" in reports[0].getMessage()
