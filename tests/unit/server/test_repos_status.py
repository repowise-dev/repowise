"""Tests for GET /api/repos workspace_status determination (Issue #2139).

Verifies that registered-but-never-indexed repositories report 'needs_index'
based on actual database index presence (GraphNode file rows) rather than
the presence of a local .repowise/wiki.db file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.models import GraphNode
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


@pytest.mark.asyncio
async def test_registered_unindexed_repo_reports_needs_index(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A repo registered in DB but with 0 file nodes reports needs_index."""
    repo_dir = Path(tempfile.mkdtemp()) / "unindexed-repo"
    repo_dir.mkdir()

    repo = await upsert_repository(session, name="unindexed-repo", local_path=str(repo_dir))
    await session.commit()

    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    data = resp.json()

    target = next((r for r in data if r["id"] == repo.id), None)
    assert target is not None
    assert target["workspace_status"] == "needs_index"


@pytest.mark.asyncio
async def test_registered_indexed_repo_without_wiki_db_reports_indexed_on_shared_db(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Under PostgreSQL (or no local wiki.db file), presence of file nodes signals indexed."""
    repo_dir = Path(tempfile.mkdtemp()) / "indexed-repo"
    repo_dir.mkdir()
    # Explicitly ensure no .repowise/wiki.db exists
    assert not (repo_dir / ".repowise" / "wiki.db").exists()

    repo = await upsert_repository(session, name="indexed-repo", local_path=str(repo_dir))
    session.add(
        GraphNode(
            repository_id=repo.id,
            node_id="src/app.py",
            node_type="file",
            symbol_count=10,
        )
    )
    await session.commit()

    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    data = resp.json()

    target = next((r for r in data if r["id"] == repo.id), None)
    assert target is not None
    # In non-workspace mode, indexed repos have workspace_status == None (not falsely 'needs_index')
    assert target["workspace_status"] is None


@pytest.mark.asyncio
async def test_workspace_mode_unindexed_member_is_not_overwritten_to_indexed(
    client: AsyncClient, session: AsyncSession, app
) -> None:
    """Workspace attach pass must not unconditionally stamp workspace_status='indexed'."""
    ws_root = Path(tempfile.mkdtemp())
    member_path = ws_root / "services" / "payment"
    member_path.mkdir(parents=True)

    # Register member in database with 0 file nodes (e.g. POST /api/repos with index: false)
    repo = await upsert_repository(session, name="payment-service", local_path=str(member_path))
    await session.commit()

    ws_config = WorkspaceConfig(
        version=1,
        repos=[
            RepoEntry(path="services/payment", alias="payment", is_primary=True),
        ],
        default_repo="payment",
    )
    app.state.workspace_config = ws_config
    app.state.workspace_root = str(ws_root)

    try:
        resp = await client.get("/api/repos")
        assert resp.status_code == 200
        data = resp.json()

        target = next((r for r in data if r["id"] == repo.id), None)
        assert target is not None
        assert target["workspace_alias"] == "payment"
        assert target["is_primary"] is True
        # Must honestly stay needs_index, NOT overwritten to indexed
        assert target["workspace_status"] == "needs_index"
    finally:
        app.state.workspace_config = None
        app.state.workspace_root = None


@pytest.mark.asyncio
async def test_workspace_mode_indexed_member_reports_indexed(
    client: AsyncClient, session: AsyncSession, app
) -> None:
    """Workspace member with file nodes in DB reports workspace_status='indexed'."""
    ws_root = Path(tempfile.mkdtemp())
    member_path = ws_root / "services" / "auth"
    member_path.mkdir(parents=True)

    repo = await upsert_repository(session, name="auth-service", local_path=str(member_path))
    session.add(
        GraphNode(
            repository_id=repo.id,
            node_id="src/main.py",
            node_type="file",
            symbol_count=5,
        )
    )
    await session.commit()

    ws_config = WorkspaceConfig(
        version=1,
        repos=[
            RepoEntry(path="services/auth", alias="auth", is_primary=False),
        ],
        default_repo="auth",
    )
    app.state.workspace_config = ws_config
    app.state.workspace_root = str(ws_root)

    try:
        resp = await client.get("/api/repos")
        assert resp.status_code == 200
        data = resp.json()

        target = next((r for r in data if r["id"] == repo.id), None)
        assert target is not None
        assert target["workspace_alias"] == "auth"
        assert target["workspace_status"] == "indexed"
    finally:
        app.state.workspace_config = None
        app.state.workspace_root = None


@pytest.mark.asyncio
async def test_missing_local_directory_reports_missing_dir(
    client: AsyncClient, session: AsyncSession
) -> None:
    """If local_path does not exist on disk, workspace_status reports missing_dir."""
    nonexistent_path = "/nonexistent/path/to/repo/xyz123"

    repo = await upsert_repository(session, name="ghost-repo", local_path=nonexistent_path)
    await session.commit()

    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    data = resp.json()

    target = next((r for r in data if r["id"] == repo.id), None)
    assert target is not None
    assert target["workspace_status"] == "missing_dir"
