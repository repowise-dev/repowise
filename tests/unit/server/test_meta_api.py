"""Tests for the /api/meta endpoints (version freshness + changelog)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import repowise.core.upgrade.release as rel


@pytest.fixture(autouse=True)
def _stub_pypi(monkeypatch, tmp_path):
    """Keep these tests offline and deterministic."""
    monkeypatch.setattr(rel, "_CACHE_PATH", tmp_path / "update-check.json")
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: ("9.9.9", None))


@pytest.mark.asyncio
async def test_version_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_version"]
    assert body["latest_version"] == "9.9.9"
    assert body["update_available"] is True
    assert body["upgrade_command"]
    # No repo_id -> store fields stay unset.
    assert body["store_format_version"] is None
    assert body["reindex_recommended"] is False


@pytest.mark.asyncio
async def test_version_endpoint_up_to_date(client: AsyncClient, monkeypatch) -> None:
    from repowise.server import __version__

    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (__version__, None))
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    assert resp.json()["update_available"] is False


@pytest.mark.asyncio
async def test_version_endpoint_unknown_when_offline(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(rel, "fetch_latest_version", lambda timeout=2.0: (None, "offline"))
    resp = await client.get("/api/meta/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_version"] is None
    assert body["update_available"] is None


@pytest.mark.asyncio
async def test_version_endpoint_unknown_repo_id_is_graceful(client: AsyncClient) -> None:
    """An unresolvable repo_id must not error; store fields stay unset."""
    resp = await client.get("/api/meta/version", params={"repo_id": "does-not-exist"})
    assert resp.status_code == 200
    assert resp.json()["store_format_version"] is None


@pytest.mark.asyncio
async def test_version_endpoint_populates_store_status(client: AsyncClient, tmp_path) -> None:
    """A resolvable repo with a state.json fills the store-format fields."""
    import json

    from tests.unit.server.conftest import create_test_repo

    repo = await create_test_repo(client, tmp_path)
    repo_dir = tmp_path / "test-repo"
    (repo_dir / ".repowise").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".repowise" / "state.json").write_text(
        json.dumps({"store_format_version": 1, "written_by_version": "0.21.0"}),
        encoding="utf-8",
    )

    resp = await client.get("/api/meta/version", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["store_format_version"] == 1
    assert body["store_compatible"] is True
    assert body["reindex_recommended"] is False


@pytest.mark.asyncio
async def test_changelog_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/meta/changelog", params={"limit": 3})
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert 0 < len(entries) <= 3
    first = entries[0]
    assert first["version"]
    assert isinstance(first["sections"], list)
