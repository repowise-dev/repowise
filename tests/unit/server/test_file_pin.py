"""Tests for POST /api/repos/{id}/files/{path}/pin-doc (issue #812).

The pin is what makes a hand-requested doc keep being regenerated: pinned
pages always enter the generation selection. The endpoint must (a) pin an
existing page and (b) create + pin a lightweight template row for a file
with no page yet.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_pin_creates_a_wanted_row_for_an_undocumented_file(
    client: AsyncClient, app
) -> None:
    """A file with no page gets a pinned template row so the next
    generation phase produces its doc."""
    repo = await create_test_repo(client)
    resp = await client.post(f"/api/repos/{repo['id']}/files/src/missing.py/pin-doc")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True

    async with get_session(app.state.session_factory) as session:
        page = await session.get(Page, "file_page:src/missing.py")
        assert page is not None
        assert page.pinned is True
        assert page.provider_name == "template"  # reads as unwritten


@pytest.mark.asyncio
async def test_pin_marks_an_existing_page(client: AsyncClient, app) -> None:
    """An existing wiki page is pinned in place, not replaced."""
    repo = await create_test_repo(client)
    from datetime import UTC, datetime

    async with get_session(app.state.session_factory) as session:
        session.add(
            Page(
                id="file_page:src/known.py",
                repository_id=repo["id"],
                page_type="file_page",
                title="known.py",
                content="real prose",
                target_path="src/known.py",
                source_hash="abc",
                model_name="claude",
                provider_name="anthropic",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    resp = await client.post(f"/api/repos/{repo['id']}/files/src/known.py/pin-doc")
    assert resp.status_code == 200
    assert resp.json()["pinned"] is True

    async with get_session(app.state.session_factory) as session:
        page = await session.get(Page, "file_page:src/known.py")
        assert page is not None
        assert page.pinned is True
        assert page.content == "real prose"  # untouched


@pytest.mark.asyncio
async def test_pin_unknown_repo_404s(client: AsyncClient) -> None:
    resp = await client.post("/api/repos/does-not-exist/files/src/a.py/pin-doc")
    assert resp.status_code == 404
