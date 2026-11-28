"""Page routes in a store several repositories share.

Two repositories with a file at the same path hold the same page id. A route
told which repository answers with that one's page; one told nothing answers
with the only page by that id, or says the id is ambiguous instead of picking.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import Page
from tests.unit.server.conftest import create_test_repo

_PID = "file_page:src/shared.py"


async def _seed(app, repo_id: str, content: str, page_id: str = _PID) -> None:
    async with get_session(app.state.session_factory) as session:
        now = datetime.now(UTC)
        session.add(
            Page(
                id=page_id,
                repository_id=repo_id,
                page_type="file_page",
                title="shared.py",
                content=content,
                target_path=page_id.split(":", 1)[1],
                source_hash="abc",
                model_name="claude",
                provider_name="anthropic",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_lookup_names_the_repository_or_is_told_the_id_is_ambiguous(
    client: AsyncClient, app
) -> None:
    first = await create_test_repo(client)
    second = await create_test_repo(client)
    await _seed(app, first["id"], "first repo prose")
    await _seed(app, second["id"], "second repo prose")

    for repo, content in ((first, "first repo prose"), (second, "second repo prose")):
        resp = await client.get(
            "/api/pages/lookup", params={"page_id": _PID, "repo_id": repo["id"]}
        )
        assert resp.status_code == 200
        assert (resp.json()["repository_id"], resp.json()["content"]) == (repo["id"], content)

    resp = await client.get("/api/pages/lookup", params={"page_id": _PID})
    assert resp.status_code == 409
    assert sorted(resp.json()["detail"]["repository_ids"]) == sorted(
        [first["id"], second["id"]]
    )


@pytest.mark.asyncio
async def test_an_id_only_one_repository_holds_needs_no_repository(
    client: AsyncClient, app
) -> None:
    first = await create_test_repo(client)
    await create_test_repo(client)
    await _seed(app, first["id"], "only prose", page_id="file_page:src/only.py")

    resp = await client.get("/api/pages/lookup", params={"page_id": "file_page:src/only.py"})
    assert resp.status_code == 200
    assert resp.json()["repository_id"] == first["id"]


@pytest.mark.asyncio
async def test_notes_and_versions_touch_only_the_named_repositorys_page(
    client: AsyncClient, app
) -> None:
    first = await create_test_repo(client)
    second = await create_test_repo(client)
    await _seed(app, first["id"], "first repo prose")
    await _seed(app, second["id"], "second repo prose")

    resp = await client.patch(
        "/api/pages/lookup/notes",
        params={"page_id": _PID},
        json={"human_notes": "ambiguous"},
    )
    assert resp.status_code == 409

    resp = await client.patch(
        "/api/pages/lookup/notes",
        params={"page_id": _PID, "repo_id": second["id"]},
        json={"human_notes": "second only"},
    )
    assert resp.status_code == 200
    async with get_session(app.state.session_factory) as session:
        mine = await crud.get_page(session, _PID, repository_id=second["id"])
        theirs = await crud.get_page(session, _PID, repository_id=first["id"])
        assert (mine.human_notes, theirs.human_notes) == ("second only", None)

    resp = await client.get(
        "/api/pages/lookup/versions", params={"page_id": _PID, "repo_id": first["id"]}
    )
    assert resp.status_code == 200
    assert resp.json() == []
