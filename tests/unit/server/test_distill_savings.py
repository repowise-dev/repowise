"""Unit tests for GET /api/repos/{repo_id}/distill-savings."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from repowise.core.distill.store import OmissionStore

from .conftest import create_test_repo


def _seed_store(repo_dir: Path) -> None:
    store = OmissionStore(repo_dir / ".repowise" / "omissions" / "omissions.db")
    store.record_saving(
        filter_name="test_output",
        source="hook",
        command="pytest",
        raw_tokens=10_000,
        distilled_tokens=1_000,
    )
    store.record_saving(
        filter_name="git_log",
        source="cli",
        command="git log",
        raw_tokens=2_000,
        distilled_tokens=200,
    )
    store.close()


async def test_savings_endpoint_returns_rollups(client: AsyncClient, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    _seed_store(Path(repo["local_path"]))

    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["events"] == 2
    assert data["saved_tokens"] == 10_800
    assert data["pricing_model"]
    assert data["estimated_usd_saved"] > 0
    filters = {row["group"]: row for row in data["per_filter"]}
    assert filters["test_output"]["saved_tokens"] == 9_000
    assert filters["git_log"]["events"] == 1
    assert len(data["per_day"]) == 1  # both events landed today


async def test_savings_endpoint_no_store_is_unavailable(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["events"] == 0
    assert data["saved_tokens"] == 0


async def test_savings_endpoint_since_filter(client: AsyncClient, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _seed_store(repo_dir)
    # Backdate one event past the filter window.
    import sqlite3

    db = repo_dir / ".repowise" / "omissions" / "omissions.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE savings SET created_at = created_at - 10 * 86400 WHERE filter = 'git_log'")
    conn.commit()
    conn.close()

    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=1)).isoformat()
    resp = await client.get(f"/api/repos/{repo['id']}/distill-savings", params={"since": cutoff})
    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == 1
    assert [row["group"] for row in data["per_filter"]] == ["test_output"]


async def test_savings_endpoint_unknown_repo_404(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nope/distill-savings")
    assert resp.status_code == 404
