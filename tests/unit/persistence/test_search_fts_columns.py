"""``page_fts`` indexes a page's summary and target path, not only its prose.

Two fields decide whether a page is the right answer and could not cause it to
be found. ``summary`` is a fresh LLM paraphrase written for every page, and it
is already part of what the answer tool's coverage re-ranker reads — a field
that reorders results but cannot produce one. ``target_path`` is the page's
file, and most page titles are that path verbatim, so a question that names a
directory or a file had almost nothing to match against.

These tests cover the upgrade as much as the behaviour: an FTS5 table cannot
be altered, so an index built by an older repowise has to be rebuilt and
refilled from ``wiki_pages`` rather than left on the old shape.
"""

from __future__ import annotations

import pytest
from sqlalchemy.sql import text

from repowise.core.persistence.crud import upsert_page
from repowise.core.persistence.search import FullTextSearch
from tests.unit.persistence.helpers import insert_repo

_OLD_SCHEMA_DDL = "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, content)"


async def _fts_columns(engine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("PRAGMA table_info(page_fts)"))
        return [r[1] for r in rows.fetchall()]


async def _downgrade_to_old_schema(engine) -> None:
    """Put the store back on the three-column table an older repowise wrote."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS page_fts"))
        await conn.execute(text(_OLD_SCHEMA_DDL))


async def _seed_page(session, repo_id: str, **overrides) -> None:
    kwargs = {
        "page_id": "file_page:src/main.py",
        "repository_id": repo_id,
        "page_type": "file_page",
        "title": "File: src/main.py",
        "content": "# Overview\n\nEntry point for the application.",
        "summary": "Boots the application and wires the request router.",
        "target_path": "src/main.py",
        "source_hash": "abc123",
        "model_name": "mock",
        "provider_name": "mock",
    }
    kwargs.update(overrides)
    await upsert_page(session, **kwargs)
    await session.commit()


@pytest.fixture
async def repo(async_session):
    return await insert_repo(async_session)


async def test_ensure_index_upgrades_old_schema_and_backfills(async_engine, async_session, repo):
    """An index written by an older repowise gains both columns, populated.

    The rebuild reads ``wiki_pages``, which is the only place the two new
    fields exist — the old FTS rows never carried them. A silent no-op here
    would leave the store on the old shape forever, because the table is
    created with ``IF NOT EXISTS``.
    """
    await _seed_page(async_session, repo.id)
    await _downgrade_to_old_schema(async_engine)
    async with async_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO page_fts(page_id, title, content) VALUES (:p, :t, :c)"),
            {"p": "file_page:src/main.py", "t": "File: src/main.py", "c": "# Overview"},
        )

    await FullTextSearch(async_engine).ensure_index()

    assert await _fts_columns(async_engine) == [
        "page_id",
        "title",
        "content",
        "summary",
        "target_path",
    ]
    async with async_engine.connect() as conn:
        rows = await conn.execute(text("SELECT page_id, summary, target_path FROM page_fts"))
        indexed = rows.fetchall()
    assert len(indexed) == 1
    assert indexed[0][1] == "Boots the application and wires the request router."
    assert indexed[0][2] == "src/main.py"


async def test_summary_only_match_returns_the_page(async_engine, async_session, repo):
    """A word that appears in the summary and nowhere else still finds the page."""
    await _seed_page(
        async_session,
        repo.id,
        summary="Reconciles the changelog against the release manifest.",
    )
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index(
        "file_page:src/main.py",
        "File: src/main.py",
        "# Overview\n\nEntry point for the application.",
        summary="Reconciles the changelog against the release manifest.",
        target_path="src/main.py",
    )

    results = await fts.search("release manifest")

    assert [r.page_id for r in results] == ["file_page:src/main.py"]


async def test_path_shaped_query_matches_on_target_path_alone(async_engine, async_session, repo):
    """A question naming a directory hits, even when the prose never says it.

    Titles are the same path in most of the corpus, so without this column a
    path-shaped question depends on whichever directory names the generated
    prose happens to mention.
    """
    await _seed_page(
        async_session,
        repo.id,
        page_id="file_page:packages/telemetry/collector.py",
        title="Collector",
        content="# Overview\n\nGathers counters and flushes them on a timer.",
        summary="Gathers counters and flushes them.",
        target_path="packages/telemetry/collector.py",
    )
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index(
        "file_page:packages/telemetry/collector.py",
        "Collector",
        "# Overview\n\nGathers counters and flushes them on a timer.",
        summary="Gathers counters and flushes them.",
        target_path="packages/telemetry/collector.py",
    )

    results = await fts.search("telemetry collector")

    assert [r.page_id for r in results] == ["file_page:packages/telemetry/collector.py"]


async def test_index_without_the_new_fields_still_writes_a_row(async_engine):
    """The two new arguments are optional, so an old caller keeps working."""
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index("p1", "Title", "searchable body text")

    results = await fts.search("searchable body")

    assert [r.page_id for r in results] == ["p1"]


async def test_index_without_the_new_fields_warns_once(async_engine, caplog):
    """A missed call site writes a healthy-looking, half-searchable row.

    Nine places in repowise write this index. One left on the old signature
    produces pages that cannot be found by their summary or their path, with
    nothing failing anywhere, so the omission is logged. Once per instance —
    every one of those call sites writes in a loop over the whole corpus.
    """
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()

    with caplog.at_level("WARNING", logger="repowise.core.persistence.search"):
        await fts.index("p1", "Title", "body")
        await fts.index("p2", "Title", "body")

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "summary or target_path" in warnings[0].getMessage()


async def test_index_with_an_empty_summary_does_not_warn(async_engine):
    """An empty string is a real value — some page types have no summary."""
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index("p1", "Title", "body", summary="", target_path="")

    assert fts._warned_missing_fields is False


async def test_rebuild_refuses_to_shrink_the_index(async_engine, async_session, repo):
    """The upgrade drops the table, so it must not run against a thinner source.

    The rebuild refills from ``wiki_pages``. If that table cannot account for
    the rows already indexed — a store whose two halves have drifted apart, or
    an engine pointed at the wrong file — dropping and refilling silently
    deletes searchable pages. Raise instead, and leave the old index in place.
    """
    await _downgrade_to_old_schema(async_engine)
    async with async_engine.begin() as conn:
        for i in range(3):
            await conn.execute(
                text("INSERT INTO page_fts(page_id, title, content) VALUES (:p, :t, :c)"),
                {"p": f"orphan-{i}", "t": "Orphan", "c": "no wiki_pages row backs this"},
            )

    with pytest.raises(RuntimeError, match="page_fts"):
        await FullTextSearch(async_engine).ensure_index()

    async with async_engine.connect() as conn:
        rows = await conn.execute(text("SELECT count(*) FROM page_fts"))
        assert rows.scalar() == 3
    assert await _fts_columns(async_engine) == ["page_id", "title", "content"]
