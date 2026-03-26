"""Tests for dead code endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session

from tests.unit.server.conftest import create_test_repo


async def _insert_dead_code(session_factory, repo_id: str) -> str:
    """Insert test dead code findings and return a finding ID."""
    async with get_session(session_factory) as session:
        await crud.save_dead_code_findings(
            session,
            repo_id,
            [
                {
                    "kind": "unreachable_file",
                    "file_path": "src/dead_module.py",
                    "symbol_name": None,
                    "symbol_kind": None,
                    "confidence": 0.9,
                    "reason": "No imports found",
                    "lines": 50,
                    "safe_to_delete": True,
                    "primary_owner": "Alice",
                    "age_days": 180,
                },
                {
                    "kind": "unused_export",
                    "file_path": "src/utils.py",
                    "symbol_name": "old_helper",
                    "symbol_kind": "function",
                    "confidence": 0.5,
                    "reason": "No callers",
                    "lines": 10,
                    "safe_to_delete": False,
                    "primary_owner": "Bob",
                    "age_days": 90,
                },
            ],
        )

    # Fetch findings to get the ID
    async with get_session(session_factory) as session:
        findings = await crud.get_dead_code_findings(session, repo_id)
        return findings[0].id


@pytest.mark.asyncio
async def test_list_dead_code_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/dead-code")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_dead_code_with_data(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/dead-code")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_list_dead_code_filter_by_kind(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/dead-code",
        params={"kind": "unreachable_file"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["kind"] == "unreachable_file"


@pytest.mark.asyncio
async def test_list_dead_code_safe_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/dead-code",
        params={"safe_only": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["safe_to_delete"] is True


@pytest.mark.asyncio
async def test_dead_code_summary(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/dead-code/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_findings"] == 2
    assert data["deletable_lines"] == 50  # Only the safe-to-delete finding
    assert "unreachable_file" in data["by_kind"]


@pytest.mark.asyncio
async def test_analyze_dead_code(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.post(f"/api/repos/{repo['id']}/dead-code/analyze")
    assert resp.status_code == 202
    assert resp.json()["status"] == "analyzing"


@pytest.mark.asyncio
async def test_resolve_finding(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    finding_id = await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/dead-code/{finding_id}",
        json={"status": "acknowledged", "note": "Will fix later"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "acknowledged"
    assert data["note"] == "Will fix later"


@pytest.mark.asyncio
async def test_resolve_finding_not_found(client: AsyncClient) -> None:
    resp = await client.patch(
        "/api/dead-code/nonexistent",
        json={"status": "resolved"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_finding_invalid_status(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    finding_id = await _insert_dead_code(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/dead-code/{finding_id}",
        json={"status": "invalid_status"},
    )
    assert resp.status_code == 400
