"""Tests for the docs counts in /api/repos/{id}/overview-summary.

The Overview's Docs tile reports total generated pages plus how many of them
are the deterministic template ("Auto") coverage tail, so a reader can tell
model-written docs from structure-derived ones. ``provider_name == "template"``
is the discriminator, matching the page schema's ``is_deterministic``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from tests.unit.server.conftest import create_test_repo

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _page(repo_id: str, path: str, provider: str) -> Page:
    return Page(
        id=f"file_page:{path}",
        repository_id=repo_id,
        page_type="file_page",
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
async def test_docs_counts_split_ai_from_template_pages(client: AsyncClient, app) -> None:
    """Total counts every page; auto counts only the template-provider ones."""
    repo = await create_test_repo(client)

    async with get_session(app.state.session_factory) as session:
        session.add(_page(repo["id"], "a.py", "anthropic"))
        session.add(_page(repo["id"], "b.py", "anthropic"))
        session.add(_page(repo["id"], "c.py", "template"))

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 3
    assert stats["doc_auto_page_count"] == 1


@pytest.mark.asyncio
async def test_docs_counts_are_zero_without_pages(client: AsyncClient) -> None:
    """An index-only repo has no wiki: both counts read zero, not null."""
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")
    assert resp.status_code == 200
    stats = resp.json()["stats"]

    assert stats["doc_page_count"] == 0
    assert stats["doc_auto_page_count"] == 0
