"""Tools served over a store opened by ``embed.open_store``: read-only, no checkout."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from repowise.server.mcp_server import embed


@pytest.fixture
async def published(setup_mcp, engine, fts) -> tuple[Path, str]:
    """The populated fixture store, indexed and checkpointed as an indexer publishes it."""
    await fts.index(
        "repo_overview:test-repo",
        "Test Repo Overview",
        "The ocelot gateway routes requests, stores records and reports usage nightly.",
        summary="ocelot gateway",
        target_path="test-repo",
    )
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    return Path(engine.url.database), setup_mcp


async def _serve(ctx, tool: str, **kwargs) -> dict:
    import repowise.server.mcp_server as mcp_mod

    embed.release(kwargs.pop("_repo_id"))  # no reply reused across the two contexts
    with embed.serving(ctx, telemetry=False):
        return await getattr(mcp_mod, tool)(**kwargs)


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("get_overview", {}),
        ("search_codebase", {"query": "AuthService", "mode": "symbol"}),
        ("get_answer", {"question": "what does the ocelot gateway do?"}),
        ("get_context", {"targets": ["src/auth/service.py"]}),
    ],
)
async def test_a_read_only_store_serves_what_a_writable_one_does(
    published: tuple[Path, str], engine, tool: str, kwargs: dict
) -> None:
    path, repo_id = published
    assert (await embed.publishable(path)).ok
    store = await embed.open_store(path, repo_id)
    try:
        read_only = await _serve(store.context("default"), tool, _repo_id=repo_id, **kwargs)
        writable = await _serve(
            embed.embedded_context(engine, repo_id, alias="default"),
            tool,
            _repo_id=repo_id,
            **kwargs,
        )
    finally:
        await store.dispose()
    assert "error" not in read_only
    for reply in (read_only, writable):
        reply.pop("_meta", None)
    assert read_only == writable


async def test_a_store_without_page_fts_degrades_search_instead_of_erroring(
    published: tuple[Path, str],
) -> None:
    import contextlib
    import sqlite3

    path, repo_id = published
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE page_fts")
        conn.commit()
    assert (await embed.publishable(path)).problems == ("page_fts_missing",)
    store = await embed.open_store(path, repo_id)
    try:
        reply = await _serve(
            store.context("default"),
            "get_answer",
            _repo_id=repo_id,
            question="what does the ocelot gateway do?",
        )
    finally:
        await store.dispose()
    assert "error" not in reply
    assert reply["answer"]
