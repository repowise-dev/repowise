"""Concept search reaches indexed files that have no wiki page.

A file is found by its file name (the filename leg) or by its symbol names (the
symbol leg). Synthetic files in TypeScript (kebab), Python (snake) and Go
(camel), plus distractors that share one word with a query or match only
through directories.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import GraphNode, Page, WikiSymbol
from repowise.server.mcp_server import _graph_files
from repowise.server.mcp_server._prose_symbols import filename_backed_pages

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

# path, symbol, language. None of these has a page.
_PAGELESS = [
    ("src/config/exec-command-highlighting.ts", "resolveExecCommandHighlighting", "typescript"),
    ("app/ingest/dynamic_import_resolver.py", "resolve_dynamic_import", "python"),
    ("internal/queue/retryBackoffPolicy.go", "NextRetryDelay", "go"),
    # Distractors.
    ("src/config/highlighting.ts", "highlight", "typescript"),
    ("src/exec/command/highlighting/index.ts", "run", "typescript"),
    ("app/ingest/import_cache_eviction_policy_table.py", "evict", "python"),
    ("src/config/exec-command-highlighting.test.ts", "check", "typescript"),
]

_QUERIES = [
    ("exec command highlighting", "src/config/exec-command-highlighting.ts"),
    ("how are dynamic imports resolved", "app/ingest/dynamic_import_resolver.py"),
    ("which retry backoff policy applies", "internal/queue/retryBackoffPolicy.go"),
]


def _page(rid: str, path: str, i: int) -> Page:
    return Page(
        id=f"file_page:{path}",
        repository_id=rid,
        page_type="file_page",
        title=f"File: {path}",
        content="overview",
        target_path=path,
        source_hash=f"fh{i}",
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        confidence=0.5,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _seed_pageless(session, rid: str) -> None:
    for i, (path, name, language) in enumerate(_PAGELESS):
        session.add(
            GraphNode(
                id=f"pl{i}",
                repository_id=rid,
                node_id=path,
                node_type="file",
                language=language,
                symbol_count=1,
                created_at=_NOW,
            )
        )
        session.add(
            WikiSymbol(
                id=f"pls{i}",
                repository_id=rid,
                file_path=path,
                symbol_id=f"{path}::{name}",
                name=name,
                qualified_name=name,
                kind="function",
                signature=f"{name}()",
                start_line=12,
                end_line=20,
                docstring="",
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language=language,
                parent_name=None,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
    await session.commit()


@pytest.fixture
async def ctx(session, repo_id, factory, tmp_path):
    await _seed_pageless(session, repo_id)
    # One paged file, so pages and pageless rows meet in one result.
    session.add(_page(repo_id, "internal/queue/retryBackoffPolicy.go", 0))
    await session.commit()
    return SimpleNamespace(session_factory=factory, path=str(tmp_path))


async def _paths(ctx, question: str) -> list[tuple[str, str]]:
    rows = await filename_backed_pages(ctx, question, max_files=8)
    return [(r["target_path"], r["page_type"]) for r in rows]


@pytest.mark.asyncio
async def test_file_name_leg_covers_files_with_and_without_a_page(ctx):
    assert (await _paths(ctx, "exec command highlighting"))[0] == (
        "src/config/exec-command-highlighting.ts",
        "file",
    )
    assert (await _paths(ctx, "which retry backoff policy applies"))[0] == (
        "internal/queue/retryBackoffPolicy.go",
        "file_page",
    )


@pytest.mark.asyncio
async def test_inflected_query_words_still_spell_the_name(ctx):
    rows = await filename_backed_pages(ctx, "where do we highlight an exec command", max_files=8)
    assert rows[0]["target_path"] == "src/config/exec-command-highlighting.ts"
    assert rows[0]["full_cover"]
    partial = await filename_backed_pages(ctx, "command highlighting", max_files=8)
    assert not partial[0]["full_cover"]


@pytest.mark.asyncio
async def test_one_shared_word_reaches_nothing(ctx):
    assert await _paths(ctx, "how is highlighting configured") == []


@pytest.mark.asyncio
async def test_directory_words_and_long_names_do_not_count(ctx):
    paths = [p for p, _ in await _paths(ctx, "exec command highlighting")]
    assert "src/exec/command/highlighting/index.ts" not in paths
    assert "src/config/highlighting.ts" not in paths
    # import + policy are two of the five words in the table's name.
    assert await _paths(ctx, "import policy") == []


@pytest.fixture
async def pageless_mcp(setup_mcp, session):
    await _seed_pageless(session, setup_mcp)
    return setup_mcp


def _by_path(result: dict) -> dict[str, dict]:
    return {r["path"]: r for r in result["results"] if r.get("path")}


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "path"), _QUERIES)
async def test_prose_query_returns_a_file_that_has_no_page(pageless_mcp, question, path):
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase(question, mode="concept", limit=10)
    row = _by_path(result)[path]
    assert row["page_type"] == "file"
    assert row["symbols"][0].endswith(":12")
    assert {"filename", "symbol"} & set(row["sources"])
    assert "page_id" not in row
    assert path in [c["path"] for c in result.get("candidates", [])]


@pytest.mark.asyncio
async def test_pageless_rows_leave_the_order_of_pages_alone(pageless_mcp, monkeypatch):
    from repowise.server.mcp_server import search_codebase, tool_search

    query = "auth service middleware exec command highlighting"
    with_rows = await search_codebase(query, mode="concept", limit=10)

    symbol_search = tool_search._safe_symbol_search
    name_search = tool_search._safe_filename_search

    async def symbols_paged_only(ctx, q, *, pageless=False):
        return await symbol_search(ctx, q)

    async def names_paged_only(ctx, q):
        return [(r, full) for r, full in await name_search(ctx, q) if r.page_type != "file"]

    monkeypatch.setattr(tool_search, "_safe_symbol_search", symbols_paged_only)
    monkeypatch.setattr(tool_search, "_safe_filename_search", names_paged_only)
    without = await search_codebase(query, mode="concept", limit=10)

    paged = [r["path"] for r in with_rows["results"] if r.get("page_type") != "file"]
    assert any(r.get("page_type") == "file" for r in with_rows["results"])
    assert paged == [r.get("path") for r in without["results"]][: len(paged)]


@pytest.mark.asyncio
async def test_filters_apply_to_pageless_rows(pageless_mcp, tmp_path):
    from repowise.server.mcp_server import search_codebase

    target = "src/config/exec-command-highlighting.ts"
    query = "exec command highlighting"
    assert target not in _by_path(await search_codebase(query, mode="concept", kind="test"))
    assert target not in _by_path(
        await search_codebase(query, mode="concept", page_type="file_page")
    )
    implementation = await search_codebase(query, mode="concept", kind="implementation")
    assert "src/config/exec-command-highlighting.test.ts" not in _by_path(implementation)

    (tmp_path / ".gitignore").write_text("src/config/\n", encoding="utf-8")
    assert target not in _by_path(await search_codebase(query, mode="concept", limit=10))


@pytest.mark.asyncio
async def test_a_pageless_test_file_ranks_below_its_source(pageless_mcp):
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase("exec command highlighting", mode="concept", limit=10)
    paths = [r.get("path") for r in result["results"]]
    test_file = "src/config/exec-command-highlighting.test.ts"
    assert paths.index("src/config/exec-command-highlighting.ts") < paths.index(test_file)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["AuthService", "resolveExecCommandHighlighting"])
async def test_identifier_query_is_unchanged_by_pageless_rows(pageless_mcp, monkeypatch, query):
    from repowise.server.mcp_server import search_codebase, tool_search

    with_legs = await search_codebase(query)

    async def no_leg(ctx, question, **_):
        return []

    monkeypatch.setattr(tool_search, "_safe_filename_search", no_leg)
    monkeypatch.setattr(tool_search, "_safe_symbol_search", no_leg)
    without_legs = await search_codebase(query)
    with_legs["_meta"].pop("timing", None)
    without_legs["_meta"].pop("timing", None)
    assert json.dumps(with_legs, sort_keys=True, default=str) == json.dumps(
        without_legs, sort_keys=True, default=str
    )


def test_reset_cache_frees_the_lock_from_its_event_loop():
    async def contend() -> None:
        async with _graph_files._LOCK:
            waiter = asyncio.ensure_future(_graph_files._LOCK.acquire())
            await asyncio.sleep(0)
        await waiter
        _graph_files._LOCK.release()

    asyncio.run(contend())
    _graph_files.reset_cache()
    asyncio.run(contend())


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"mode": "hybrid"}, {"mode": "concept", "repo": "all"}])
async def test_pageless_rows_ship_no_page_id_on_any_route(pageless_mcp, kwargs):
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase("exec command highlighting", limit=10, **kwargs)
    rows = [r for r in result["results"] if r.get("page_type") == "file"]
    assert rows
    assert all("page_id" not in r for r in rows)
