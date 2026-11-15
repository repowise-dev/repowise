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

    assert marked == ["file_page:src/auth/service.py"]
    await session.refresh(page)
    assert page.freshness_status == "tombstone"
    meta = json.loads(page.metadata_json)
    assert meta["successor_paths"] == ["src/auth/service_v2.py"]
    assert meta["tombstone_reason"] == "deleted"


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
    # ``page_id`` is omitted only where ``page_type`` + ``target_path`` rebuild
    # it, or ``path`` on a file page that serves ``path`` instead.
    ids = [
        r.get("page_id") or f"{r['page_type']}:{r.get('target_path') or r['path']}"
        for r in result["results"]
    ]
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


# --- Pages the build retired: the source is live -----------------------------

_SERVICE = "src/auth/service.py"
_SPOTLIGHT_ID = f"symbol_spotlight:{_SERVICE}::AuthService"


def _page(repo_id: str, page_type: str, target: str, title: str, content: str) -> Page:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 1, tzinfo=UTC)
    return Page(
        id=f"{page_type}:{target}",
        repository_id=repo_id,
        page_type=page_type,
        title=title,
        content=content,
        target_path=target,
        source_hash=f"h-{page_type}-{target}",
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        confidence=0.9,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=now,
        updated_at=now,
    )


async def _retire(session, repo_id: str, *page_ids: str) -> list[str]:
    """Run the build's retirement with *page_ids* left out of the generation."""
    from repowise.core.pipeline.persist import tombstone_pages_outside_generation

    every = set((await session.execute(select(Page.id))).scalars())
    retired = await tombstone_pages_outside_generation(session, repo_id, every - set(page_ids))
    await session.commit()
    return retired


async def _card(target: str, include: list[str]) -> dict:
    from repowise.server.mcp_server import get_context

    return (await get_context([target], include=include))["targets"][target]


@pytest.mark.asyncio
async def test_retirement_records_its_reason(setup_mcp, session):
    repo_id = setup_mcp
    assert await _retire(session, repo_id, f"file_page:{_SERVICE}") == [f"file_page:{_SERVICE}"]
    page = await session.get(Page, f"file_page:{_SERVICE}")
    await session.refresh(page)
    assert page.freshness_status == "tombstone"
    assert json.loads(page.metadata_json)["tombstone_reason"] == "retired"


@pytest.mark.asyncio
async def test_a_superseded_file_page_serves_its_live_successor(setup_mcp, session):
    repo_id = setup_mcp
    session.add(_page(repo_id, "api_contract", _SERVICE, "API Contract: auth", "# Routes"))
    await session.commit()
    await _retire(session, repo_id, f"file_page:{_SERVICE}")

    card = await _card(_SERVICE, ["docs"])

    assert "error" not in card
    assert "deleted" not in json.dumps(card)
    assert card["docs"]["title"] == "API Contract: auth"


@pytest.mark.asyncio
async def test_a_deselected_file_page_falls_through_to_the_index(setup_mcp, session):
    await _retire(session, setup_mcp, f"file_page:{_SERVICE}")

    card = await _card(_SERVICE, ["docs"])

    assert "error" not in card
    assert card["type"] == "file"
    assert "Main authentication service class" not in json.dumps(card)


@pytest.mark.asyncio
async def test_a_regrouped_module_page_is_not_reported_deleted(setup_mcp, session):
    await _retire(session, setup_mcp, "module_page:src/auth")

    card = await _card("src/auth", ["docs"])

    assert "error" not in card
    assert card["type"] == "module"
    assert "Handles authentication" not in json.dumps(card)


@pytest.mark.asyncio
async def test_a_deselected_spotlight_serves_the_file_page_entry(setup_mcp, session):
    repo_id = setup_mcp
    file_page = await session.get(Page, f"file_page:{_SERVICE}")
    file_page.content = "# service.py\n\n- `class AuthService`: Signs users in.\n"
    session.add(
        _page(repo_id, "symbol_spotlight", f"{_SERVICE}::AuthService", "AuthService", "# OLD")
    )
    await session.commit()
    target = f"{_SERVICE}::AuthService"
    assert (await _card(target, ["full_doc"]))["docs"]["documentation"] == "# OLD"

    await _retire(session, repo_id, _SPOTLIGHT_ID)

    docs = (await _card(target, ["full_doc"]))["docs"]
    assert docs["documentation"] == "- `class AuthService`: Signs users in."


@pytest.mark.asyncio
async def test_search_drops_retired_pages(setup_mcp, session):
    repo_id = setup_mcp
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.search import SearchResult
    from repowise.server.mcp_server import search_codebase

    await _retire(session, repo_id, f"file_page:{_SERVICE}")

    async def fake_search(query, limit=10):
        return [
            SearchResult(
                page_id=f"file_page:{_SERVICE}",
                title="Auth Service",
                page_type="file_page",
                target_path=_SERVICE,
                score=0.9,
                snippet="auth",
                search_type="vector",
            ),
        ]

    mcp_mod._vector_store.search = fake_search
    result = await search_codebase("auth service")
    assert all(r.get("page_id") != f"file_page:{_SERVICE}" for r in result["results"])
    assert all(r.get("path") != _SERVICE or r.get("page_type") != "file_page"
               for r in result["results"])


@pytest.mark.asyncio
async def test_only_a_deletion_hides_a_files_symbols(setup_mcp, session):
    from repowise.core.persistence.crud.tombstoned_pages import deleted_file_paths

    repo_id = setup_mcp
    middleware = "src/auth/middleware.py"
    await _retire(session, repo_id, f"file_page:{_SERVICE}")
    await mark_tombstone_pages(session, repo_id, [(middleware, [])])
    await session.commit()

    deleted = await deleted_file_paths(session, repo_id, {_SERVICE, middleware, "src/db/models.py"})

    assert deleted == {middleware}


def test_a_tombstone_without_a_reason_reads_as_a_deletion():
    from repowise.core.persistence.crud.tombstoned_pages import announces_deletion

    legacy = SimpleNamespace(freshness_status="tombstone", metadata_json='{"successor_paths": []}')
    retired = SimpleNamespace(
        freshness_status="tombstone", metadata_json='{"tombstone_reason": "retired"}'
    )
    assert announces_deletion(legacy)
    assert not announces_deletion(retired)
    assert not announces_deletion(SimpleNamespace(freshness_status="fresh", metadata_json="{}"))


@pytest.mark.asyncio
async def test_a_deleted_router_does_not_hide_behind_its_contract_page(setup_mcp, session):
    from repowise.core.persistence.crud.tombstoned_pages import deleted_file_paths

    repo_id = setup_mcp
    session.add(_page(repo_id, "api_contract", _SERVICE, "API Contract: auth", "# Routes"))
    await session.commit()
    await mark_tombstone_pages(session, repo_id, [(_SERVICE, [])])
    await session.commit()

    assert "deleted" in (await _card(_SERVICE, ["docs"]))["error"]
    assert await deleted_file_paths(session, repo_id, {_SERVICE}) == {_SERVICE}
