"""Tombstone pages for deleted/renamed files (A4).

A ``freshness_status="fresh"`` page for a file that no longer exists is an
active trap: retrieval serves it, agents cite it. These tests cover the
marking helper and every serving surface that must skip or redirect.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import mark_tombstone_pages, tombstone_candidates


class TestTombstoneCandidates:
    def test_deleted_file_has_no_successor(self) -> None:
        fd = SimpleNamespace(status="deleted", path="src/old.py", old_path=None)
        assert tombstone_candidates([fd]) == [("src/old.py", [])]

    def test_renamed_file_points_to_new_path(self) -> None:
        fd = SimpleNamespace(status="renamed", path="src/new.py", old_path="src/old.py")
        assert tombstone_candidates([fd]) == [("src/old.py", ["src/new.py"])]

    def test_modified_and_added_ignored(self) -> None:
        fds = [
            SimpleNamespace(status="modified", path="a.py", old_path=None),
            SimpleNamespace(status="added", path="b.py", old_path=None),
        ]
        assert tombstone_candidates(fds) == []


@pytest.mark.asyncio
async def test_mark_tombstone_pages_sets_status_and_successors(setup_mcp, session):
    repo_id = setup_mcp
    page = (
        (await session.execute(select(Page).where(Page.target_path == "src/auth/service.py")))
        .scalars()
        .first()
    )
    assert page is not None, "fixture must seed a page for src/auth/service.py"

    marked = await mark_tombstone_pages(
        session, repo_id, [("src/auth/service.py", ["src/auth/service_v2.py"])]
    )
    await session.commit()

    assert marked == 1
    await session.refresh(page)
    assert page.freshness_status == "tombstone"
    assert json.loads(page.metadata_json)["successor_paths"] == ["src/auth/service_v2.py"]


@pytest.mark.asyncio
async def test_get_context_returns_tombstone_redirect(setup_mcp, session):
    repo_id = setup_mcp
    from repowise.server.mcp_server import get_context

    await mark_tombstone_pages(
        session, repo_id, [("src/auth/service.py", ["src/auth/service_v2.py"])]
    )
    await session.commit()

    result = await get_context(["src/auth/service.py"], include=["docs"])
    t = result["targets"]["src/auth/service.py"]
    assert "tombstone" in t["error"]
    assert t["successor_paths"] == ["src/auth/service_v2.py"]
    assert "service_v2" in t["hint"]


@pytest.mark.asyncio
async def test_search_drops_tombstoned_pages(setup_mcp, session, monkeypatch):
    repo_id = setup_mcp
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.search import SearchResult
    from repowise.server.mcp_server import search_codebase

    await mark_tombstone_pages(session, repo_id, [("src/auth/service.py", [])])
    await session.commit()

    async def fake_search(query, limit=10):
        return [
            SearchResult(
                page_id="file_page:src/auth/service.py",
                title="Auth Service",
                page_type="file_page",
                target_path="src/auth/service.py",
                score=0.9,
                snippet="auth",
                search_type="vector",
            ),
            SearchResult(
                page_id="file_page:src/db/models.py",
                title="DB Models",
                page_type="file_page",
                target_path="src/db/models.py",
                score=0.5,
                snippet="models",
                search_type="vector",
            ),
        ]

    mcp_mod._vector_store.search = fake_search
    result = await search_codebase("auth service")
    ids = [r["page_id"] for r in result["results"]]
    assert "file_page:src/auth/service.py" not in ids
    assert "file_page:src/db/models.py" in ids


@pytest.mark.asyncio
async def test_answer_hydration_drops_tombstoned_pages(setup_mcp, session):
    repo_id = setup_mcp
    from repowise.server.mcp_server._answer_pipeline import hydrate_hits

    await mark_tombstone_pages(session, repo_id, [("src/auth/service.py", [])])
    await session.commit()

    import repowise.server.mcp_server as mcp_mod

    ctx = SimpleNamespace(session_factory=mcp_mod._session_factory)
    hits = [
        {"page_id": "file_page:src/auth/service.py", "score": 5.0},
        {"page_id": "file_page:src/db/models.py", "score": 4.0},
    ]
    out = await hydrate_hits(hits, ctx)
    paths = [h["target_path"] for h in out]
    assert "src/auth/service.py" not in paths
    assert "src/db/models.py" in paths
