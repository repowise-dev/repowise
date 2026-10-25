"""/api/repos keeps exclude_patterns and wiki_style in the repo's config.yaml."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.repo_config import load_repo_config
from tests.unit.server.conftest import create_test_repo


def _config_path(repo: dict) -> Path:
    return Path(repo["local_path"]) / ".repowise" / "config.yaml"


async def _row_settings(session_factory, repo_id: str) -> dict:
    async with session_factory() as s:
        row = await crud.get_repository(s, repo_id)
        return json.loads(row.settings_json)


@pytest.mark.asyncio
async def test_patch_writes_config_yaml_and_keeps_other_keys(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    path = _config_path(repo)
    path.parent.mkdir(parents=True)
    path.write_text("provider: openai\n", encoding="utf-8")

    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"settings": {"exclude_patterns": ["dist/"], "wiki_style": "caveman", "a": 1}},
    )

    assert resp.status_code == 200
    assert resp.json()["settings"] == {
        "a": 1,
        "exclude_patterns": ["dist/"],
        "wiki_style": "caveman",
    }
    assert load_repo_config(repo["local_path"]) == {
        "provider": "openai",
        "exclude_patterns": ["dist/"],
        "wiki_style": "caveman",
    }
    assert await _row_settings(session_factory, repo["id"]) == {"a": 1}


@pytest.mark.asyncio
async def test_get_reads_a_cli_edit_of_config_yaml(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    await client.patch(f"/api/repos/{repo['id']}", json={"settings": {"wiki_style": "caveman"}})
    _config_path(repo).write_text(
        "wiki_style: reference\nexclude_patterns:\n- vendor/\n", encoding="utf-8"
    )

    settings = (await client.get(f"/api/repos/{repo['id']}")).json()["settings"]

    assert settings == {"wiki_style": "reference", "exclude_patterns": ["vendor/"]}


@pytest.mark.asyncio
async def test_legacy_row_values_show_then_move_into_config_on_save(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    async with session_factory() as s:
        row = await crud.get_repository(s, repo["id"])
        row.settings_json = json.dumps({"wiki_style": "tutorial", "exclude_patterns": ["old/"]})
        await s.commit()

    shown = (await client.get(f"/api/repos/{repo['id']}")).json()["settings"]
    assert shown == {"wiki_style": "tutorial", "exclude_patterns": ["old/"]}

    resp = await client.patch(
        f"/api/repos/{repo['id']}", json={"settings": {"exclude_patterns": ["new/"]}}
    )

    assert resp.status_code == 200
    assert load_repo_config(repo["local_path"]) == {
        "wiki_style": "tutorial",
        "exclude_patterns": ["new/"],
    }
    assert await _row_settings(session_factory, repo["id"]) == {}


@pytest.mark.asyncio
async def test_settings_without_file_keys_keep_legacy_row_values(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    async with session_factory() as s:
        row = await crud.get_repository(s, repo["id"])
        row.settings_json = json.dumps({"wiki_style": "tutorial"})
        await s.commit()

    resp = await client.patch(f"/api/repos/{repo['id']}", json={"settings": {"a": 1}})

    assert resp.status_code == 200
    assert await _row_settings(session_factory, repo["id"]) == {"a": 1, "wiki_style": "tutorial"}
    assert not _config_path(repo).exists()


@pytest.mark.asyncio
async def test_missing_checkout_is_an_error_not_a_row_write(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    shutil.rmtree(repo["local_path"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"name": "renamed", "settings": {"wiki_style": "caveman"}},
    )

    assert resp.status_code == 409
    assert "not reachable" in resp.json()["detail"]
    async with session_factory() as s:
        row = await crud.get_repository(s, repo["id"])
        assert row.name == "test-repo"
        assert "wiki_style" not in json.loads(row.settings_json)


@pytest.mark.asyncio
async def test_unwritable_config_is_an_error(client: AsyncClient, monkeypatch) -> None:
    repo = await create_test_repo(client)

    def _deny(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("repowise.core.repo_config.save_repo_config", _deny)
    resp = await client.patch(
        f"/api/repos/{repo['id']}", json={"settings": {"wiki_style": "caveman"}}
    )

    assert resp.status_code == 409
    assert "Permission denied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_broken_config_yaml_is_a_400_and_left_alone(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    path = _config_path(repo)
    path.parent.mkdir(parents=True)
    path.write_text("- not a mapping\n", encoding="utf-8")

    resp = await client.patch(
        f"/api/repos/{repo['id']}", json={"settings": {"wiki_style": "caveman"}}
    )

    assert resp.status_code == 400
    assert "config.yaml" in resp.json()["detail"]
    assert path.read_text(encoding="utf-8") == "- not a mapping\n"
    # A listing still renders with the broken file in place.
    assert (await client.get("/api/repos")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [{"exclude_patterns": "dist/"}, {"exclude_patterns": ["ok/", 3]}, {"wiki_style": 5}],
)
async def test_malformed_values_are_rejected(client: AsyncClient, settings: dict) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(f"/api/repos/{repo['id']}", json={"settings": settings})
    assert resp.status_code == 400
    assert not _config_path(repo).exists()


@pytest.mark.asyncio
async def test_create_writes_registration_style_to_config_yaml(
    client: AsyncClient, tmp_path: Path, session_factory
) -> None:
    repo_dir = tmp_path / "r"
    (repo_dir / ".git").mkdir(parents=True)

    resp = await client.post(
        "/api/repos",
        json={
            "index": False,
            "name": "r",
            "local_path": str(repo_dir),
            "settings": {"wiki_style": "caveman"},
        },
    )

    assert resp.status_code == 201
    assert resp.json()["settings"] == {"wiki_style": "caveman"}
    assert load_repo_config(repo_dir) == {"wiki_style": "caveman"}
    assert await _row_settings(session_factory, resp.json()["id"]) == {}


@pytest.mark.asyncio
async def test_unchanged_save_leaves_config_bytes_alone(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    path = _config_path(repo)
    path.parent.mkdir(parents=True)
    path.write_text("provider: openai\nexclude_patterns:\n- dist/\n", encoding="utf-8")
    before = path.read_bytes()

    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"settings": {"exclude_patterns": ["dist/"], "wiki_style": "comprehensive"}},
    )

    assert resp.status_code == 200
    assert path.read_bytes() == before
