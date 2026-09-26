"""Overview language_count matches stats: code languages only."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from tests.unit.server.conftest import create_test_repo


@pytest.mark.anyio
async def test_overview_language_count_matches_code_languages_only(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with get_session(session_factory) as session:
        session.add_all(
            [
                GraphNode(
                    repository_id=repo_id,
                    node_id="a.py",
                    node_type="file",
                    language="python",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="b.ts",
                    node_type="file",
                    language="typescript",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="README.md",
                    node_type="file",
                    language="markdown",
                ),
                GraphNode(
                    repository_id=repo_id,
                    node_id="package.json",
                    node_type="file",
                    language="json",
                ),
            ]
        )
        await session.commit()

    resp = await client.get(f"/api/repos/{repo_id}/overview-summary")
    assert resp.status_code == 200
    body = resp.json()

    # Composition bar still lists every format.
    assert {row["language"] for row in body["languages"]} == {
        "python",
        "typescript",
        "markdown",
        "json",
    }
    # Tile count matches stats_highlights: .py + .ts only → 2.
    assert body["stats"]["language_count"] == 2
