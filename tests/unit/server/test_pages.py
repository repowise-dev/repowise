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
async def test_list_pages_defaults_to_full_rows(client: AsyncClient, app) -> None:
    """No `fields` means what it always meant — content and metadata included."""
    repo_id, _ = await _create_page(client, app.state.session_factory)
    resp = await client.get("/api/pages", params={"repo_id": repo_id})
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["content"] == "# Main module\n\nEntry point."
    assert "metadata" in row


@pytest.mark.asyncio
async def test_list_pages_summary_drops_the_heavy_fields(
    client: AsyncClient, app
) -> None:
    repo_id, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get(
        "/api/pages", params={"repo_id": repo_id, "fields": "summary"}
    )
    assert resp.status_code == 200
    row = resp.json()[0]
    assert "content" not in row
    assert "metadata" not in row
    # Everything a list actually renders is still there.
    assert row["id"] == page_id
    assert row["title"] == "main.py"
    assert row["target_path"] == "src/main.py"
    assert row["content_chars"] == len("# Main module\n\nEntry point.")


@pytest.mark.asyncio
async def test_list_pages_rejects_unknown_fields(client: AsyncClient, app) -> None:
    repo_id, _ = await _create_page(client, app.state.session_factory)
    resp = await client.get(
        "/api/pages", params={"repo_id": repo_id, "fields": "titles"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_lookup_accepts_repo_id(client: AsyncClient, app) -> None:
    """The session is routed by repo_id, so a lookup that knows the repo says
    so — without it a workspace server searches the wrong store."""
    repo_id, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get(
        "/api/pages/lookup", params={"page_id": page_id, "repo_id": repo_id}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == page_id


@pytest.mark.asyncio
async def test_lookup_reaches_a_second_workspace_store(client: AsyncClient, app) -> None:
    """In workspace mode each repo has its own database and the primary one
    cannot see the others' rows. A page there is reachable only when the
    request carries the repo_id the session is routed by."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence.database import init_db

    other_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(other_engine)
    other_factory = async_sessionmaker(
        other_engine, expire_on_commit=False, class_=AsyncSession
    )
    other_repo_id = "b" * 32
    app.state.workspace_sessions = {other_repo_id: other_factory}

    async with get_session(other_factory) as session:
        await crud.upsert_repository(
            session, name="other", local_path="/tmp/other", repo_id=other_repo_id
        )
        await crud.upsert_page(
            session,
            page_id="file_page:src/only_over_here.py",
            repository_id=other_repo_id,
            page_type="file_page",
            title="only_over_here.py",
            content="Lives in the second store.",
            target_path="src/only_over_here.py",
            source_hash="h",
            model_name="mock",
            provider_name="mock",
        )

    try:
        params = {"page_id": "file_page:src/only_over_here.py"}
        # Without repo_id the request lands on the primary store, which has
        # never heard of this page.
        assert (await client.get("/api/pages/lookup", params=params)).status_code == 404

        scoped = await client.get(
            "/api/pages/lookup", params={**params, "repo_id": other_repo_id}
        )
        assert scoped.status_code == 200
        assert scoped.json()["content"] == "Lives in the second store."
    finally:
        await other_engine.dispose()


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
async def test_list_pages_has_prose_filter(client: AsyncClient, app) -> None:
    """?has_prose splits written concept pages from stubs, scoped to the
    model-written types. A structural file page (never has prose) is in neither
    filtered view but present when the filter is omitted."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(app.state.session_factory) as session:
        # A concept-tree stub: a model-written type still rendered as a template.
        await crud.upsert_page(
            session,
            page_id="module_page:stub",
            repository_id=repo_id,
            page_type="module_page",
            title="stub",
            content="stub page",
            target_path="src/stub",
            source_hash="",
            model_name="template",
            provider_name="template",
        )
        # A written concept page.
        await crud.upsert_page(
            session,
            page_id="module_page:written",
            repository_id=repo_id,
            page_type="module_page",
            title="written",
            content="model page",
            target_path="src/written",
            source_hash="h",
            model_name="gpt",
            provider_name="openai",
        )
        # A structural file page: template forever, outside the has_prose axis.
        await crud.upsert_page(
            session,
            page_id="file_page:f.py",
            repository_id=repo_id,
            page_type="file_page",
            title="f",
            content="file page",
            target_path="f.py",
            source_hash="",
            model_name="template",
            provider_name="template",
        )

    stubs = await client.get("/api/pages", params={"repo_id": repo_id, "has_prose": "false"})
    assert {p["id"] for p in stubs.json()} == {"module_page:stub"}

    written = await client.get("/api/pages", params={"repo_id": repo_id, "has_prose": "true"})
    assert {p["id"] for p in written.json()} == {"module_page:written"}

    both = await client.get("/api/pages", params={"repo_id": repo_id})
    assert len(both.json()) == 3


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

# ---------------------------------------------------------------------------
# Retired page ids
#
# Wiki pages are public and linkable.  A page that stops being generated has to
# keep resolving, or every inbound link to it breaks silently on the next index.
# ---------------------------------------------------------------------------


_REDIRECTS = "repowise.server.routers.pages.resolve_superseded"


@pytest.mark.asyncio
async def test_retired_page_id_serves_its_successor(client: AsyncClient, app) -> None:
    _, page_id = await _create_page(client, app.state.session_factory)
    with patch(_REDIRECTS, return_value=page_id):
        resp = await client.get("/api/pages/architecture_diagram:gone")
    assert resp.status_code == 200
    body = resp.json()
    # The reader gets the successor, and can see that they moved: the id in the
    # body is the successor's, not the one they asked for.
    assert body["id"] == page_id
    assert resp.headers["x-repowise-redirected-from"] == "architecture_diagram:gone"


@pytest.mark.asyncio
async def test_retired_page_id_redirects_on_lookup_too(client: AsyncClient, app) -> None:
    """The query-param form is the one the UI uses; it must behave the same."""
    _, page_id = await _create_page(client, app.state.session_factory)
    with patch(_REDIRECTS, return_value=page_id):
        resp = await client.get(
            "/api/pages/lookup", params={"page_id": "architecture_diagram:gone"}
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == page_id
    assert resp.headers["x-repowise-redirected-from"] == "architecture_diagram:gone"


@pytest.mark.asyncio
async def test_live_page_is_not_redirected(client: AsyncClient, app) -> None:
    """A page that exists is served as itself and never consults the table."""
    _, page_id = await _create_page(client, app.state.session_factory)
    resp = await client.get(f"/api/pages/{page_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == page_id
    assert "x-repowise-redirected-from" not in resp.headers


@pytest.mark.asyncio
async def test_unknown_page_id_still_404s(client: AsyncClient, app) -> None:
    """Nothing in the redirect path may turn a genuine miss into a success."""
    await _create_page(client, app.state.session_factory)
    resp = await client.get("/api/pages/file_page:does/not/exist.py")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_successor_that_does_not_exist_404s(client: AsyncClient, app) -> None:
    """A redirect pointing at a missing page is a miss, not a 500."""
    await _create_page(client, app.state.session_factory)
    with patch(_REDIRECTS, return_value="file_page:also/missing.py"):
        resp = await client.get("/api/pages/architecture_diagram:gone")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_broken_redirect_table_does_not_500_the_reader(client: AsyncClient, app) -> None:
    """A cycle is a bug, but it must degrade to a 404 rather than a crash."""
    from repowise.core.generation.page_redirects import SupersededCycleError

    await _create_page(client, app.state.session_factory)
    with patch(_REDIRECTS, side_effect=SupersededCycleError("a -> b -> a")):
        resp = await client.get("/api/pages/architecture_diagram:gone")
    assert resp.status_code == 404
