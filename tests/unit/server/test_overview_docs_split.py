"""Tests for the docs counts in /api/repos/{id}/overview-summary.

Two counts: every generated page, and the subset carrying model-written prose.

The old `doc_auto_page_count` split was retired with the provenance axis
(#1037) and is not coming back. Its problem was the denominator: it labelled
every `provider_name == "template"` page "auto", which swept in the whole file
layer — pages no model was ever going to write — so the split really said
"file pages vs everything else" while claiming to say "who wrote this".

`doc_prose_page_count` asks a narrower and answerable question — did a model
write this page — using the provider alone, with no page-type carve-out. That
keeps the label honest whatever the generator does next, and total minus prose
is exactly "pages built from the index".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from tests.unit.server.conftest import create_test_repo

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _page(repo_id: str, path: str, provider: str, page_type: str = "file_page") -> Page:
    return Page(
        id=f"{page_type}:{path}",
        repository_id=repo_id,
        page_type=page_type,
        title=path,
        content=f"# {path}",
        summary="",
        target_path=path,
        source_hash=f"h-{path}",
        model_name="mock",
        provider_name=provider,
        generation_level=4,
        confidence=0.9,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_docs_count_totals_every_page(client: AsyncClient, app) -> None:
    """The Docs tile counts every generated page, of every provenance."""
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        session.add(_page(repo["id"], "a.py", "anthropic"))
        session.add(_page(repo["id"], "b.py", "anthropic"))
        session.add(_page(repo["id"], "c.py", "template"))

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 3
    assert "doc_auto_page_count" not in stats


@pytest.mark.asyncio
async def test_docs_count_is_zero_without_pages(client: AsyncClient) -> None:
    """An index-only repo has no wiki: the count reads zero, not null."""
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 0
    assert stats["doc_prose_page_count"] == 0


@pytest.mark.asyncio
async def test_prose_count_follows_the_provider_not_the_page_type(
    client: AsyncClient, app
) -> None:
    """A model-written page counts as prose whatever type it is.

    Scoping the count to "page types a model usually writes" looks more
    principled and is wrong in practice: file and symbol pages *do* get
    generated with a real provider on some runs, and excluding them would file
    model-written pages under "built from the index" — the exact mislabel this
    count exists to avoid.
    """
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        session.add(_page(repo["id"], "a.py", "openai"))
        session.add(_page(repo["id"], "b.py", "template"))
        session.add(_page(repo["id"], "core", "anthropic", page_type="module_page"))

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 3
    assert stats["doc_prose_page_count"] == 2


@pytest.mark.asyncio
async def test_stubbed_pages_are_not_prose(client: AsyncClient, app) -> None:
    """A page the template stub produced carries no prose.

    Covers the fallback case too: when a provider call fails the page keeps
    `provider_name = "template"`, and counting it as prose would report a
    generation outage as documentation the repo does not have.
    """
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        session.add(_page(repo["id"], "core", "template", page_type="module_page"))
        session.add(_page(repo["id"], "api", "anthropic", page_type="module_page"))

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 2
    assert stats["doc_prose_page_count"] == 1
