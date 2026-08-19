"""``reindex`` writes the same vector generation would have written.

It used to build its own text — the title, a newline, the content — while
generation embedded the content alone and neither carried the page's path or
summary. Reindexing a wiki therefore changed what its vectors meant, and
nothing reported it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repowise.cli.commands import reindex_cmd
from repowise.core.persistence.vector_store import embed_item


class _DummyEngine:
    async def dispose(self) -> None:
        return None


class _Page:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    """Serves the page rows on the first query and nothing after.

    ``_reindex`` opens one session for pages and another for decision
    records, so the counter is shared across sessions rather than held per
    session — otherwise the pages come back a second time as decisions.
    """

    def __init__(self, rows: list[Any], state: dict) -> None:
        self._rows = rows
        self._state = state

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, _stmt: object) -> _Result:
        self._state["calls"] = self._state.get("calls", 0) + 1
        return _Result(self._rows if self._state["calls"] == 1 else [])


class _RecordingStore:
    """Captures the items handed to the store instead of embedding them."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.items: list[tuple[str, str, dict]] = []

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        self.items.extend(items)

    async def embed_and_upsert(self, page_id: str, text: str, meta: dict) -> None:
        self.items.append((page_id, text, meta))

    async def close(self) -> None:
        return None


@pytest.fixture
def run_reindex(monkeypatch, tmp_path: Path):
    """Run ``_reindex`` over *rows* and return the store that recorded it."""

    async def _run(rows: list[Any]) -> _RecordingStore:
        store = _RecordingStore()

        state: dict = {}

        def sessionmaker(*_a: object, **_kw: object):
            return lambda: _Session(rows, state)

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(
            reindex_cmd, "get_db_url_for_repo", lambda _p: "sqlite+aiosqlite:///:memory:"
        )
        monkeypatch.setattr(
            "repowise.core.persistence.database.create_engine", lambda _url: _DummyEngine()
        )

        async def _noop(_engine: object) -> None:
            return None

        monkeypatch.setattr("repowise.core.persistence.database.init_db", _noop)
        monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", sessionmaker)
        monkeypatch.setattr(
            "repowise.core.persistence.vector_store.LanceDBVectorStore",
            lambda *a, **kw: store,
        )

        await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)
        return store

    return _run


async def test_reindex_embeds_the_shared_recipe(run_reindex) -> None:
    page = _Page(
        id="file_page:packages/core/search.py",
        title="File: packages/core/search.py",
        page_type="file_page",
        target_path="packages/core/search.py",
        summary="Full-text search over the wiki index.",
        content="## Overview\n\nBuilds the query and ranks the rows.",
    )

    store = await run_reindex([page])

    assert store.items == [
        embed_item(
            page.id,
            title=page.title,
            page_type=page.page_type,
            target_path=page.target_path,
            summary=page.summary,
            content=page.content,
        )
    ]


async def test_reindex_carries_the_path_and_summary_into_the_text(run_reindex) -> None:
    """The two fields it never used to pass, named directly.

    Asserting equality against the helper alone would still pass if the
    helper itself dropped them.
    """
    page = _Page(
        id="file_page:a/b/widget.py",
        title="File: a/b/widget.py",
        page_type="file_page",
        target_path="a/b/widget.py",
        summary="Draws the widget.",
        content="Body with neither word in it.",
    )

    store = await run_reindex([page])

    _pid, text, meta = store.items[0]
    assert "a/b/widget.py" in text
    assert "Draws the widget." in text
    assert meta["summary"] == "Draws the widget."


async def test_a_titleless_page_is_skipped_not_indexed(run_reindex) -> None:
    """One unusable row must not abort a whole reindex, and must not be
    written either: a vector with no title cannot be found by name."""
    rows = [
        _Page(
            id="file_page:nameless.py",
            title="",
            page_type="file_page",
            target_path="nameless.py",
            summary="",
            content="Body.",
        ),
        _Page(
            id="file_page:fine.py",
            title="File: fine.py",
            page_type="file_page",
            target_path="fine.py",
            summary="",
            content="Body.",
        ),
    ]

    store = await run_reindex(rows)

    assert [item[0] for item in store.items] == ["file_page:fine.py"]
