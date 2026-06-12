"""Tests for the /api/repos/{repo_id}/external-systems dependency registry."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import ExternalSystem
from tests.unit.server.conftest import create_test_repo


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        session.add_all(
            [
                ExternalSystem(
                    repository_id=repo_id,
                    name="react",
                    display_name="React",
                    ecosystem="npm",
                    category="framework",
                    version="^19.0.0",
                    declared_in="packages/web/package.json",
                    is_dev_dep=False,
                ),
                ExternalSystem(
                    repository_id=repo_id,
                    name="vitest",
                    display_name="Vitest",
                    ecosystem="npm",
                    category="tool",
                    version="^4.1.5",
                    declared_in="packages/ui/package.json",
                    is_dev_dep=True,
                ),
                ExternalSystem(
                    repository_id=repo_id,
                    name="fastapi",
                    display_name="FastAPI",
                    ecosystem="pypi",
                    category="framework",
                    version=">=0.110",
                    declared_in="packages/server/pyproject.toml",
                    is_dev_dep=False,
                ),
            ]
        )
        await session.flush()


@pytest.mark.asyncio
async def test_registry_lists_all_rows(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/external-systems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["prod_count"] == 2
    assert data["dev_count"] == 1
    assert data["ecosystems"] == ["npm", "pypi"]
    assert data["manifests"] == [
        "packages/server/pyproject.toml",
        "packages/ui/package.json",
        "packages/web/package.json",
    ]
    # Sorted by category prominence (framework first), then name.
    assert [e["name"] for e in data["items"]] == ["fastapi", "react", "vitest"]
    react = data["items"][1]
    assert react["display_name"] == "React"
    assert react["version"] == "^19.0.0"
    assert react["declared_in"] == "packages/web/package.json"
    assert react["is_dev_dep"] is False


@pytest.mark.asyncio
async def test_registry_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/external-systems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
