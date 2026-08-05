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
from repowise.core.persistence.search import PAGE_FTS_COLUMNS, FullTextSearch
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


async def _insert_old_rows(engine, page_ids: list[str]) -> None:
    async with engine.begin() as conn:
        for pid in page_ids:
            await conn.execute(
                text("INSERT INTO page_fts(page_id, title, content) VALUES (:p, :t, :c)"),
                {"p": pid, "t": pid, "c": f"body of {pid}"},
            )


async def _indexed_ids(engine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT page_id FROM page_fts"))
        return {r[0] for r in rows.fetchall()}


async def test_rebuild_discards_orphans_instead_of_refusing(
    async_engine, async_session, repo
):
    """An index holding more rows than ``wiki_pages`` still upgrades (#1309).

    The excess is orphans: rows whose page was swept from SQL while the FTS
    delete that should have followed never ran. Refusing to rebuild protected
    rows that already point at nothing, and did it from inside the call every
    command makes to open the store — including ``doctor --repair``, which the
    error then told the user to run.
    """
    await _seed_page(async_session, repo.id)
    await _downgrade_to_old_schema(async_engine)
    await _insert_old_rows(
        async_engine,
        ["file_page:src/main.py", "orphan-0", "orphan-1", "orphan-2"],
    )

    await FullTextSearch(async_engine).ensure_index()

    assert await _fts_columns(async_engine) == list(PAGE_FTS_COLUMNS)
    assert await _indexed_ids(async_engine) == {"file_page:src/main.py"}


async def test_rebuild_reports_the_orphans_it_dropped(async_engine, async_session, repo, caplog):
    """Silently discarding indexed rows is how the next drift goes unnoticed."""
    await _seed_page(async_session, repo.id)
    await _downgrade_to_old_schema(async_engine)
    await _insert_old_rows(async_engine, ["file_page:src/main.py", "orphan-0", "orphan-1"])

    with caplog.at_level("WARNING", logger="repowise.core.persistence.search"):
        await FullTextSearch(async_engine).ensure_index()

    assert any("2 orphaned page_fts row" in r.getMessage() for r in caplog.records)


async def test_rebuild_survives_an_index_with_no_pages_behind_it(async_engine):
    """Every row orphaned is the extreme of the same case, not a special one."""
    await _downgrade_to_old_schema(async_engine)
    await _insert_old_rows(async_engine, ["orphan-0", "orphan-1"])

    await FullTextSearch(async_engine).ensure_index()

    assert await _fts_columns(async_engine) == list(PAGE_FTS_COLUMNS)
    assert await _indexed_ids(async_engine) == set()


async def test_rebuild_leaves_the_old_index_alone_when_wiki_pages_is_missing(async_engine):
    """No source to refill from is the one case where rebuilding loses data.

    It must not raise either: ``ensure_index`` is on the way in to every
    command, so an exception here takes down a CLI whose index still answers
    queries perfectly well on its old shape.
    """
    await _downgrade_to_old_schema(async_engine)
    await _insert_old_rows(async_engine, ["p1"])
    async with async_engine.begin() as conn:
        await conn.execute(text("DROP TABLE wiki_pages"))

    await FullTextSearch(async_engine).ensure_index()

    assert await _fts_columns(async_engine) == ["page_id", "title", "content"]
    assert await _indexed_ids(async_engine) == {"p1"}


async def test_prune_orphans_removes_rows_whose_page_is_gone(
    async_engine, async_session, repo
):
    """The residue of a sweep whose FTS delete never ran.

    Six call sites delete pages from SQL and their index rows afterwards,
    outside the transaction, best-effort. Anything that ends the process in
    between leaves a row that answers queries in full and 404s when opened.
    """
    await _seed_page(async_session, repo.id)
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index("file_page:src/main.py", "Main", "body", summary="s", target_path="p")
    await fts.index("swept:gone", "Gone", "body of a swept page", summary="s", target_path="p")

    assert await fts.prune_orphans() == 1
    assert await _indexed_ids(async_engine) == {"file_page:src/main.py"}


async def test_prune_orphans_is_a_no_op_on_a_clean_store(async_engine, async_session, repo):
    """It runs on every command, so the common case must not touch the index."""
    await _seed_page(async_session, repo.id)
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index("file_page:src/main.py", "Main", "body", summary="s", target_path="p")

    assert await fts.prune_orphans() == 0
    assert await _indexed_ids(async_engine) == {"file_page:src/main.py"}


async def test_ensure_index_prunes_orphans_on_a_current_schema(
    async_engine, async_session, repo
):
    """The self-heal cannot depend on there being a column upgrade to do.

    A store already on the current shape is where the orphans of an
    interrupted sweep land from now on, and nothing else ever reconciles them.
    """
    await _seed_page(async_session, repo.id)
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.index("swept:gone", "Gone", "body", summary="s", target_path="p")

    await FullTextSearch(async_engine).ensure_index()

    assert await _indexed_ids(async_engine) == set()
