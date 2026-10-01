"""The Fix-first REST routes: the queue, one item by id, and the scope control."""

from __future__ import annotations

from repowise.core.persistence.crud import upsert_repository
from tests.unit.persistence.test_fix_first_loader import seed_fix_first

from .conftest import create_test_repo


async def _repo(client, session, tmp_path) -> str:
    repo = await create_test_repo(client, tmp_path)
    await upsert_repository(session, name="r", local_path=repo["local_path"])
    return repo["id"]


async def test_queue_then_one_item_by_id(client, session, tmp_path) -> None:
    repo_id = await _repo(client, session, tmp_path)
    await seed_fix_first(session, repo_id)
    await session.commit()

    body = (await client.get(f"/api/repos/{repo_id}/health/fix-first?limit=2")).json()
    assert body["totals"]["shown"] == 2 and body["totals"]["eligible"] == 3
    assert body["lead"] == body["items"][0]
    lead_id = body["lead"]["id"]

    item = (await client.get(f"/api/repos/{repo_id}/health/fix-first/{lead_id}")).json()
    assert item["id"] == lead_id and item["rank"] == 0
    missing = await client.get(f"/api/repos/{repo_id}/health/fix-first/fix1_missing")
    assert missing.status_code == 404


async def test_unknown_scope_is_rejected(client, session, tmp_path) -> None:
    repo_id = await _repo(client, session, tmp_path)
    await session.commit()
    resp = await client.get(f"/api/repos/{repo_id}/health/fix-first?scope=tests")
    assert resp.status_code == 422
