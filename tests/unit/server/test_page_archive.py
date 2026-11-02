"""Native retired-page readers expose history without restoring obsolete live ids."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs

OLD_ID = "module_page:src/old"
NEW_ID = "module_page:src/new"


async def _retired_repo(session_factory):
    async with get_session(session_factory) as session:
        repo = await insert_repo(session)
        kwargs = make_page_kwargs(
            repo.id,
            page_id=OLD_ID,
            page_type="module_page",
            target_path="src/old",
            content="Earlier original prose",
        )
        await crud.upsert_page(session, **kwargs)
        await crud.upsert_page(session, **{**kwargs, "content": "Complete retired prose"})
        old = await crud.get_page(session, OLD_ID)
        old.human_notes = "Original human notes"
        await session.flush()
        await crud.upsert_page(
            session,
            **make_page_kwargs(
                repo.id,
                page_id=NEW_ID,
                page_type="module_page",
                target_path="src/new",
                content="New live prose",
            ),
        )
        await crud.retire_pages(session, repo.id, [OLD_ID], "superseded", {OLD_ID: NEW_ID})
        return repo.id


async def test_native_archive_list_and_complete_detail(client, app):
    repo_id = await _retired_repo(app.state.session_factory)
    base = f"/api/repos/{repo_id}/pages/retired"
    listing = await client.get(base)
    assert listing.status_code == 200
    summary = listing.json()
    assert len(summary) == 1
    assert summary[0]["page_id"] == OLD_ID
    assert summary[0]["reason"] == "superseded"
    assert summary[0]["superseded_by"] == NEW_ID
    assert "page" not in summary[0]
    detail = await client.get(f"{base}/{quote(OLD_ID, safe='')}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["page"]["content"] == "Complete retired prose"
    assert data["page"]["human_notes"] == "Original human notes"
    assert [v["content"] for v in data["versions"]] == ["Earlier original prose"]
    live = await client.get("/api/pages", params={"repo_id": repo_id})
    assert [page["id"] for page in live.json()] == [NEW_ID]


async def test_retired_reader_cannot_cross_repository(client, app):
    await _retired_repo(app.state.session_factory)
    async with get_session(app.state.session_factory) as session:
        other = await insert_repo(session, name="other", local_path="/tmp/other")
    base = f"/api/repos/{other.id}/pages/retired"
    listing = await client.get(base)
    assert listing.status_code == 200
    assert listing.json() == []
    assert (await client.get(f"{base}/{quote(OLD_ID, safe='')}")).status_code == 404


@pytest.mark.parametrize("lookup", [True, False])
async def test_archived_live_link_names_its_retirement_and_successor(client, app, lookup):
    repo_id = await _retired_repo(app.state.session_factory)
    path = "/api/pages/lookup" if lookup else f"/api/pages/{quote(OLD_ID, safe='')}"
    params = {"repo_id": repo_id}
    if lookup:
        params["page_id"] = OLD_ID
    response = await client.get(path, params=params)
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["status"] == "retired"
    assert detail["reason"] == "superseded"
    assert detail["superseded_by"] == NEW_ID
    archive = await client.get(detail["archive_url"])
    assert archive.status_code == 200
    assert archive.json()["page"]["content"] == "Complete retired prose"
