"""Every server surface reports one index time: the last sync.

The MCP envelope, the health overview the extension reads, and the Overview
page all read ``crud.index_synced_at``, and a settings edit is not a sync.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._meta import freshness_from_repo
from tests.unit.server.conftest import create_test_repo


async def test_surfaces_agree_on_the_sync_time(client: AsyncClient, app) -> None:
    repo_id = (await create_test_repo(client))["id"]
    synced = (datetime.now(UTC) - timedelta(days=3)).replace(microsecond=0)
    async with get_session(app.state.session_factory) as session:
        (await crud.get_repository(session, repo_id)).updated_at = synced
        await session.commit()

    # Not a sync: must not make the index read as fresh.
    patched = await client.patch(f"/api/repos/{repo_id}", json={"settings": {"k": 1}})
    assert patched.status_code == 200

    health = (await client.get(f"/api/repos/{repo_id}/health/overview")).json()
    overview = (await client.get(f"/api/repos/{repo_id}/overview-summary")).json()
    async with get_session(app.state.session_factory) as session:
        meta = freshness_from_repo(await crud.get_repository(session, repo_id), targets=[])

    assert health["meta"]["last_indexed_at"] == synced.isoformat()
    assert overview["health"]["last_indexed_at"] == synced.isoformat()
    assert meta["index_age_days"] == 3
