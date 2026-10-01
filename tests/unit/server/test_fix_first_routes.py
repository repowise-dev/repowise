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


async def test_item_prompt_is_core_rendering_of_the_item(client, session, tmp_path) -> None:
    from repowise.core.agent_prompts import render_fix_item

    repo_id = await _repo(client, session, tmp_path)
    await seed_fix_first(session, repo_id)
    await session.commit()
    lead = (await client.get(f"/api/repos/{repo_id}/health/fix-first")).json()["lead"]
    name = (await client.get(f"/api/repos/{repo_id}")).json()["name"]

    url = f"/api/repos/{repo_id}/health/fix-first/{lead['id']}/prompt"
    body = (await client.get(url, params={"flavor": "claude-code-mcp"})).json()
    assert body == {"flavor": "claude-code-mcp", "text": render_fix_item(lead, "claude-code-mcp", name)}
    assert "## Look closer (Repowise MCP)" in body["text"]


async def test_item_prompt_rejects_bad_flavor_and_unknown_ids(client, session, tmp_path) -> None:
    repo_id = await _repo(client, session, tmp_path)
    await session.commit()
    url = f"/api/repos/{repo_id}/health/fix-first/fix1_missing/prompt"
    assert (await client.get(url, params={"flavor": "vim"})).status_code == 422
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/repos/nope/health/fix-first/fix1_missing/prompt")).status_code == 404
