"""Tests for /api/pages endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _create_page(client: AsyncClient, session_factory) -> tuple[str, str]:
    """Create a repo and a page, return (repo_id, page_id)."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with get_session(session_factory) as session:
        await crud.upsert_page(
            session,
            page_id="file_page:src/main.py",
            repository_id=repo_id,
            page_type="file_page",
            title="main.py",
            content="# Main module\n\nEntry point.",
            target_path="src/main.py",
            source_hash="abc123",
            model_name="mock",
            provider_name="mock",
        )

    return repo_id, "file_page:src/main.py"


@pytest.mark.asyncio
async def test_list_pages_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get("/api/pages", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_pages_with_data(client: AsyncClient, app) -> None:
    repo_id, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get("/api/pages", params={"repo_id": repo_id})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == page_id
    assert data[0]["title"] == "main.py"


@pytest.mark.asyncio
async def test_get_page_by_path(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get(f"/api/pages/{page_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == page_id
    assert data["content"] == "# Main module\n\nEntry point."


@pytest.mark.asyncio
async def test_get_page_by_query(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get("/api/pages/lookup", params={"page_id": page_id})
    assert resp.status_code == 200
    assert resp.json()["id"] == page_id


@pytest.mark.asyncio
async def test_get_page_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/pages/file_page:nonexistent.py")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_page_versions_empty(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get("/api/pages/lookup/versions", params={"page_id": page_id})
    assert resp.status_code == 200
    assert resp.json() == []  # First version has no archived versions


@pytest.mark.asyncio
async def test_update_page_notes_roundtrip(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)

    resp = await client.patch(
        "/api/pages/lookup/notes",
        params={"page_id": page_id},
        json={"human_notes": "Reviewed by platform team."},
    )
    assert resp.status_code == 200
    assert resp.json()["human_notes"] == "Reviewed by platform team."

    # Whitespace-only clears the note back to null.
    cleared = await client.patch(
        "/api/pages/lookup/notes",
        params={"page_id": page_id},
        json={"human_notes": "   "},
    )
    assert cleared.status_code == 200
    assert cleared.json()["human_notes"] is None


@pytest.mark.asyncio
async def test_update_page_notes_not_found(client: AsyncClient) -> None:
    resp = await client.patch(
        "/api/pages/lookup/notes",
        params={"page_id": "file_page:missing.py"},
        json={"human_notes": "x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_pages_deterministic_filter(client: AsyncClient, app) -> None:
    """?deterministic splits template (unwritten) pages from model-written ones."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_page(
            session,
            page_id="file_page:tmpl.py",
            repository_id=repo_id,
            page_type="file_page",
            title="tmpl",
            content="template page",
            target_path="tmpl.py",
            source_hash="",
            model_name="template",
            provider_name="template",
        )
        await crud.upsert_page(
            session,
            page_id="file_page:written.py",
            repository_id=repo_id,
            page_type="file_page",
            title="written",
            content="model page",
            target_path="written.py",
            source_hash="h",
            model_name="gpt",
            provider_name="openai",
        )

    unwritten = await client.get(
        "/api/pages", params={"repo_id": repo_id, "deterministic": "true"}
    )
    assert {p["id"] for p in unwritten.json()} == {"file_page:tmpl.py"}

    written = await client.get(
        "/api/pages", params={"repo_id": repo_id, "deterministic": "false"}
    )
    assert {p["id"] for p in written.json()} == {"file_page:written.py"}

    both = await client.get("/api/pages", params={"repo_id": repo_id})
    assert len(both.json()) == 2


@pytest.mark.asyncio
async def test_regenerate_page_launches_job(client: AsyncClient, app) -> None:
    """D1: the regenerate click commits the job row and launches it immediately."""
    _, page_id = await _create_page(client, app.state.session_factory)
    with patch("repowise.server.routers.repos._launch_job_task") as launch:
        resp = await client.post("/api/pages/lookup/regenerate", params={"page_id": page_id})
    assert resp.status_code == 202
    launch.assert_called_once()


@pytest.mark.asyncio
async def test_regenerate_page_returns_202(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    with patch("repowise.server.routers.repos._launch_job_task"):
        resp = await client.post("/api/pages/lookup/regenerate", params={"page_id": page_id})
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data


@pytest.mark.asyncio
async def test_regenerate_page_not_found(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/pages/lookup/regenerate",
        params={"page_id": "file_page:nonexistent.py"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_regenerate_page_with_known_style_accepted(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    with patch("repowise.server.routers.repos._launch_job_task"):
        resp = await client.post(
            "/api/pages/lookup/regenerate",
            params={"page_id": page_id, "style": "caveman"},
        )
    assert resp.status_code == 202
    assert "job_id" in resp.json()


@pytest.mark.asyncio
async def test_regenerate_page_rejects_unknown_style(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.post(
        "/api/pages/lookup/regenerate",
        params={"page_id": page_id, "style": "bogus"},
    )
    assert resp.status_code == 400
    assert "style" in resp.json()["detail"].lower()
