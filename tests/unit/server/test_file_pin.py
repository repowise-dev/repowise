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
    # The file has to exist in the checkout: the endpoint now refuses to pin a
    # row for a path the repository does not have.
    _write(repo["local_path"], "src/missing.py")
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
    _write(repo["local_path"], "src/known.py")
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

def _write(root: str, rel: str) -> None:
    """Create *rel* inside *root*, so the endpoint's existence check passes."""
    from pathlib import Path

    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_pin_404s_for_a_file_that_is_not_there(client: AsyncClient) -> None:
    """Only a real file can be pinned.

    Without this the endpoint writes a pinned page for any string a caller
    sends, and the UI renders it as a doc the user asked for even though no
    reindex will ever fill it.
    """
    repo = await create_test_repo(client)
    resp = await client.post(f"/api/repos/{repo['id']}/files/src/absent.py/pin-doc")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pin_refuses_a_path_outside_the_checkout(client: AsyncClient) -> None:
    """A ``..`` segment must not reach out of the repository."""
    repo = await create_test_repo(client)
    resp = await client.post(
        f"/api/repos/{repo['id']}/files/../../etc/hosts/pin-doc"
    )
    assert resp.status_code in (404, 400)


@pytest.mark.asyncio
async def test_pin_refuses_a_page_belonging_to_another_repository(
    client: AsyncClient, app
) -> None:
    """``Page.id`` is global, so two repositories sharing a path share an id.

    ``file_detail`` already refuses to *read* the other repository's row; this
    endpoint used to pin it, which let one repository's Doc tab mark another
    repository's page.
    """
    from datetime import UTC, datetime

    first = await create_test_repo(client)
    second = await create_test_repo(client)
    _write(second["local_path"], "src/shared.py")

    async with get_session(app.state.session_factory) as session:
        session.add(
            Page(
                id="file_page:src/shared.py",
                repository_id=first["id"],
                page_type="file_page",
                title="shared.py",
                content="belongs to the first repo",
                target_path="src/shared.py",
                source_hash="abc",
                model_name="claude",
                provider_name="anthropic",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    resp = await client.post(
        f"/api/repos/{second['id']}/files/src/shared.py/pin-doc"
    )
    assert resp.status_code == 409

    async with get_session(app.state.session_factory) as session:
        page = await session.get(Page, "file_page:src/shared.py")
        assert page is not None
        assert page.pinned in (False, None), "the other repository's page was pinned"
