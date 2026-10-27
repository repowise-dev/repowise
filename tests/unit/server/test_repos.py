"""Tests for /api/repos endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_create_repo(client: AsyncClient) -> None:
    repo_dir = Path(tempfile.mkdtemp()) / "my-repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    resp = await client.post(
        "/api/repos",
        json={
            "index": False,
            "name": "my-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/my-repo",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-repo"
    assert data["url"] == "https://github.com/example/my-repo"
    assert data["default_branch"] == "main"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_repos_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_repos_with_data(client: AsyncClient) -> None:
    await create_test_repo(client)
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "test-repo"


@pytest.mark.asyncio
async def test_get_repo_by_id(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-repo"


@pytest.mark.asyncio
async def test_get_repo_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"name": "updated-name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"


@pytest.mark.asyncio
async def test_update_repo_accepts_known_wiki_style(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"settings": {"wiki_style": "caveman"}},
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["wiki_style"] == "caveman"


@pytest.mark.asyncio
async def test_update_repo_rejects_unknown_wiki_style(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"settings": {"wiki_style": "bogus"}},
    )
    assert resp.status_code == 400
    assert "wiki_style" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_repo_returns_202(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp = await client.post(f"/api/repos/{repo['id']}/sync")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_sync_repo_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/repos/nonexistent/sync")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_full_resync_returns_202(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp = await client.post(f"/api/repos/{repo['id']}/full-resync")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data


@pytest.mark.asyncio
async def test_sync_duplicate_returns_409(client: AsyncClient) -> None:
    """A second sync while one is pending/running returns 409."""
    repo = await create_test_repo(client)

    # Mock execute_job to prevent actual background pipeline run during tests
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp1 = await client.post(f"/api/repos/{repo['id']}/sync")
        assert resp1.status_code == 202

        # Second sync should be rejected (job is still pending/running)
        resp2 = await client.post(f"/api/repos/{repo['id']}/sync")
        assert resp2.status_code == 409
        assert "already in progress" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_full_resync_duplicate_returns_409(client: AsyncClient) -> None:
    """A second full-resync while one is pending/running returns 409."""
    repo = await create_test_repo(client)

    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp1 = await client.post(f"/api/repos/{repo['id']}/full-resync")
        assert resp1.status_code == 202

        resp2 = await client.post(f"/api/repos/{repo['id']}/full-resync")
        assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_delete_repo_success(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.delete(f"/api/repos/{repo['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True

    # Verify repo is gone
    resp = await client.get(f"/api/repos/{repo['id']}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_repo_not_found(client: AsyncClient) -> None:
    resp = await client.delete("/api/repos/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_wiki_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nonexistent/export")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_wiki_returns_zip(client: AsyncClient, session) -> None:
    import zipfile
    from io import BytesIO

    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from tests.unit.persistence.helpers import make_page_kwargs

    repo = await upsert_repository(session, name="export-test", local_path="/tmp/export-test")
    await upsert_page(session, **make_page_kwargs(repo.id))
    await session.commit()

    resp = await client.get(f"/api/repos/{repo.id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) == 1
    assert names[0].startswith("wiki/")
    assert names[0].endswith(".md")


@pytest.mark.asyncio
async def test_stats_file_count_excludes_symbol_nodes(client: AsyncClient, session) -> None:
    """`file_count` must count files, not every `graph_nodes` row.

    Symbol nodes share the table. Counting them made the field report ~10x
    the real figure, which both the multi-repo dashboard and the chat empty
    state printed verbatim as "N files".
    """
    from repowise.core.persistence.crud import upsert_repository
    from repowise.core.persistence.models import GraphNode
    from tests.unit.persistence.helpers import make_wiki_symbol

    repo = await upsert_repository(session, name="counts", local_path="/tmp/counts")
    for i in range(2):
        # The parser emitted an overload pair: 6 emissions, 5 distinct symbols.
        # The stored per-file figure of a pre-dedupe index still says 6.
        session.add(
            GraphNode(
                repository_id=repo.id,
                node_id=f"src/mod{i}.py",
                node_type="file",
                symbol_count=6,
            )
        )
        for name in [f"sym{j}" for j in range(5)] + ["__module__"]:
            session.add(
                GraphNode(
                    repository_id=repo.id,
                    node_id=f"src/mod{i}.py::{name}",
                    node_type="symbol",
                )
            )
        session.add_all(make_wiki_symbol(repo.id, f"src/mod{i}.py", f"sym{j}") for j in range(5))
    # Import targets are file rows too: a third-party package and a path an
    # import names that traversal never parsed.
    for node_id, language in [("external:requests", "external"), ("src/generated", "unknown")]:
        session.add(
            GraphNode(repository_id=repo.id, node_id=node_id, node_type="file", language=language)
        )
    await session.commit()

    data = (await client.get(f"/api/repos/{repo.id}/stats")).json()
    overview = (await client.get(f"/api/repos/{repo.id}/overview-summary")).json()
    symbols = (await client.get("/api/symbols", params={"repo_id": repo.id})).json()

    assert data["file_count"] == 2  # not 16, not 4
    assert overview["stats"]["file_count"] == 2
    # One figure, the symbol index's row count: not 12 symbol nodes, not 12 emissions.
    assert data["symbol_count"] == 10
    assert overview["stats"]["symbol_count"] == 10
    assert symbols["total"] == 10


@pytest.mark.asyncio
async def test_create_on_a_registered_path_keeps_omitted_url_and_branch(
    client: AsyncClient,
) -> None:
    repo_dir = Path(tempfile.mkdtemp()) / "legacy"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    first = {"index": False, "name": "legacy", "local_path": str(repo_dir)}
    await client.post(
        "/api/repos",
        json={**first, "url": "https://github.com/acme/legacy", "default_branch": "master"},
    )

    again = await client.post("/api/repos", json={**first, "name": "renamed"})

    data = again.json()
    assert data["name"] == "renamed"
    assert data["url"] == "https://github.com/acme/legacy"
    assert data["default_branch"] == "master"

    sent = await client.post("/api/repos", json={**first, "url": "", "default_branch": "dev"})
    assert sent.json()["url"] == ""
    assert sent.json()["default_branch"] == "dev"


@pytest.mark.asyncio
async def test_indexed_create_on_a_registered_path_keeps_omitted_fields_in_both_rows(
    client: AsyncClient, app, tmp_path, monkeypatch
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository
    from repowise.server.routers import repos as repos_module

    async def _noop(job_id, app_state, session_factory_override=None):
        return None

    monkeypatch.setattr(repos_module, "execute_job", _noop)
    repo_dir = tmp_path / "legacy"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    body = {"name": "legacy", "local_path": str(repo_dir), "index": True}
    await client.post(
        "/api/repos",
        json={**body, "url": "https://github.com/acme/legacy", "default_branch": "master"},
    )

    data = (await client.post("/api/repos", json=body)).json()

    assert (data["url"], data["default_branch"]) == ("https://github.com/acme/legacy", "master")
    async with app.state.session_factory() as session:
        row = (await session.execute(select(Repository))).scalar_one()
    assert (row.url, row.default_branch) == ("https://github.com/acme/legacy", "master")


@pytest.mark.asyncio
async def test_indexed_create_after_a_metadata_only_one_keeps_its_fields(
    client: AsyncClient, app, tmp_path, monkeypatch
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository
    from repowise.server.routers import repos as repos_module

    async def _noop(job_id, app_state, session_factory_override=None):
        return None

    monkeypatch.setattr(repos_module, "execute_job", _noop)
    repo_dir = tmp_path / "legacy"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    body = {"name": "legacy", "local_path": str(repo_dir)}
    await client.post(
        "/api/repos",
        json={
            **body,
            "index": False,
            "url": "https://github.com/acme/legacy",
            "default_branch": "master",
        },
    )

    data = (await client.post("/api/repos", json={**body, "index": True})).json()

    assert (data["url"], data["default_branch"]) == ("https://github.com/acme/legacy", "master")
    async with app.state.session_factory() as session:
        row = (await session.execute(select(Repository))).scalar_one()
    assert (row.url, row.default_branch) == ("https://github.com/acme/legacy", "master")


def _checkout(root: Path, branch: str) -> Path:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    root.mkdir()
    git("init", "-b", branch)
    git("remote", "add", "origin", f"https://user:ghp_secret@github.com/acme/{root.name}.git")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "a")
    return root


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [False, True])
async def test_create_without_url_or_branch_reads_them_from_git(
    client: AsyncClient, tmp_path, monkeypatch, index: bool
) -> None:
    from repowise.server.routers import repos as repos_module

    async def _noop(job_id, app_state, session_factory_override=None):
        return None

    monkeypatch.setattr(repos_module, "execute_job", _noop)
    root = _checkout(tmp_path / "legacy", "master")

    body = {"name": "legacy", "local_path": str(root), "index": index}
    data = (await client.post("/api/repos", json=body)).json()

    assert (data["url"], data["default_branch"]) == (
        "https://github.com/acme/legacy.git",
        "master",
    )
