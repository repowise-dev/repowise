"""Tests for GET /api/repos/{id}/file-content — what it will and won't serve.

The endpoint used to serve anything under the repo root, which put
``.repowise/.env`` (the user's provider API keys) and ``.git/config`` one
query string away.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from tests.unit.server.conftest import create_test_repo


async def _index_file(session_factory, repo_id: str, node_id: str) -> None:
    async with get_session(session_factory) as session:
        session.add(
            GraphNode(
                repository_id=repo_id,
                node_type="file",
                language="python",
                node_id=node_id,
            )
        )


@pytest.mark.asyncio
async def test_serves_indexed_file(client: AsyncClient, app, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    root = Path(repo["local_path"])
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n")
    await _index_file(app.state.session_factory, repo["id"], "src/main.py")

    resp = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "src/main.py"}
    )
    assert resp.status_code == 200
    assert resp.text == "print('hi')\n"


@pytest.mark.asyncio
async def test_serves_indexed_dot_paths(client: AsyncClient, app, tmp_path: Path) -> None:
    """The traverser walks `.github` and friends, so those files stay readable.

    Only `.git/` and `.repowise/` are refused; a blanket dot rule would 400
    indexed, harmless files like workflows and `.eslintrc.json`.
    """
    repo = await create_test_repo(client, tmp_path)
    root = Path(repo["local_path"])
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    await _index_file(app.state.session_factory, repo["id"], ".github/workflows/ci.yml")

    resp = await client.get(
        f"/api/repos/{repo['id']}/file-content",
        params={"file_path": ".github/workflows/ci.yml"},
    )
    assert resp.status_code == 200
    assert resp.text == "on: push\n"


@pytest.mark.asyncio
async def test_refuses_dotfiles_holding_credentials(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    """The provider keys and the git remote config are both inside the root.

    Indexed here on purpose: the deny must not depend on the traverser
    continuing to skip those two directories.
    """
    repo = await create_test_repo(client, tmp_path)
    root = Path(repo["local_path"])
    (root / ".repowise").mkdir()
    (root / ".repowise" / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n")
    (root / ".git" / "config").write_text("[remote]\n\turl = https://user:token@host/x\n")

    for path in (".repowise/.env", ".git/config"):
        await _index_file(app.state.session_factory, repo["id"], path)

    for path in (".repowise/.env", ".git/config"):
        resp = await client.get(
            f"/api/repos/{repo['id']}/file-content", params={"file_path": path}
        )
        assert resp.status_code == 400, path
        assert "sk-ant-secret" not in resp.text
        assert "token" not in resp.text


@pytest.mark.asyncio
async def test_refuses_unindexed_file(client: AsyncClient, app, tmp_path: Path) -> None:
    """Present on disk and inside the root is not enough on its own."""
    repo = await create_test_repo(client, tmp_path)
    (Path(repo["local_path"]) / "secrets.txt").write_text("shh\n")

    resp = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "secrets.txt"}
    )
    assert resp.status_code == 404
    assert "shh" not in resp.text


@pytest.mark.asyncio
async def test_refuses_traversal_outside_the_root(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    (tmp_path / "outside.txt").write_text("nope\n")

    resp = await client.get(
        f"/api/repos/{repo['id']}/file-content", params={"file_path": "../outside.txt"}
    )
    assert resp.status_code == 400
    assert "nope" not in resp.text
