"""Tests for the docs count in /api/repos/{id}/overview-summary.

The Overview's Docs tile reports the total generated page count. The
template-vs-AI split it used to carry was retired with the provenance axis: the
file layer is structural for every repo, so a per-page "who wrote this" count
said nothing.
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
