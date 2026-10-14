"""GET /api/repos/{id}/changes and /changes/{change_id}."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core import attribution
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _row(change_id: str, day: int, **over) -> dict:
    row = {
        "change_id": change_id,
        "identity_kind": "pr",
        "identity_value": change_id.removeprefix("pr-"),
        "shape": "squash",
        "forge": "github",
        "forge_ref": "#" + change_id.removeprefix("pr-"),
        "title": f"feat: change {change_id}",
        "merged_at": datetime(2026, 1, day, tzinfo=UTC),
        "head_sha": f"{day:02d}" + "a" * 38,
        "author_name": "Ann",
        "author_email": "ann@example.com",
        "commit_shas_json": json.dumps([f"{day:02d}" + "a" * 38]),
        "files_json": json.dumps([{"path": "src/x.py", "added": 2, "deleted": 0}]),
        "files_changed": 1,
        "lines_added": 2,
        "lines_deleted": 0,
        "category": "feature",
        "provenance": "agent",
        "provenance_agent": 1,
        "provenance_known": 1,
        "metadata_source": "git",
    }
    row.update(over)
    return row


async def _seed(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_changes(
            session,
            repo_id,
            [
                _row("pr-1", 1),
                _row("pr-2", 2, category="fix", title="fix: crash"),
                _row("pr-3", 3, files_json=json.dumps([{"path": "docs/a.md", "added": 1, "deleted": 0}])),
            ],
        )


@pytest.mark.asyncio
async def test_list_is_newest_first_and_filters(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/changes"

    page = (await client.get(url)).json()
    assert page["total"] == 3 and page["attribution"] is True
    assert [c["id"] for c in page["items"]] == ["pr-3", "pr-2", "pr-1"]
    first = page["items"][0]
    assert first["schema_version"] == 1 and first["commit_count"] == 1
    assert "files" not in first
    assert first["provenance"]["label"] == "agent"

    assert [c["id"] for c in (await client.get(url, params={"category": "fix"})).json()["items"]] == [
        "pr-2"
    ]
    assert [c["id"] for c in (await client.get(url, params={"q": "docs/a"})).json()["items"]] == [
        "pr-3"
    ]


@pytest.mark.asyncio
async def test_show_by_id_or_sha(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    base = f"/api/repos/{repo['id']}/changes"

    record = (await client.get(f"{base}/pr-2")).json()
    assert record["files"] == [{"path": "src/x.py", "added": 2, "deleted": 0}]
    assert record["metadata"]["source"] == "git" and record["metadata"]["reviewers"] is None
    assert (await client.get(f"{base}/{'02' + 'a' * 8}")).json()["id"] == "pr-2"
    assert (await client.get(f"{base}/pr-99")).status_code == 404
    assert (await client.get("/api/repos/nope/changes")).status_code == 404


@pytest.mark.asyncio
async def test_attribution_off_hides_provenance(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    config = Path(repo["local_path"]) / ".repowise" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("attribution:\n  enabled: false\n", encoding="utf-8")

    page = (await client.get(f"/api/repos/{repo['id']}/changes")).json()
    assert page["attribution"] is False
    assert all(c["provenance"] is None for c in page["items"])
    assert (await client.get(f"/api/repos/{repo['id']}/changes/pr-1")).json()["provenance"] is None
