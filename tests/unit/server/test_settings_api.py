"""GET/PATCH ``/api/repos/{id}/settings``: the registry ``repowise config`` reads."""

from __future__ import annotations

from pathlib import Path

import yaml
from httpx import AsyncClient

from tests.unit.server.conftest import create_test_repo


def _config_path(repo: dict) -> Path:
    return Path(repo["local_path"]) / ".repowise" / "config.yaml"


def _write_config(repo: dict, text: str) -> None:
    path = _config_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _setting(body: dict, key: str) -> dict:
    return next(s for a in body["areas"] for s in a["settings"] if s["key"] == key)


async def test_get_lists_user_facing_settings_by_area(client: AsyncClient):
    from repowise.core.settings import AREAS, all_settings

    repo = await create_test_repo(client)
    _write_config(repo, "commit_limit: 200\n")

    resp = await client.get(f"/api/repos/{repo['id']}/settings")

    assert resp.status_code == 200
    body = resp.json()
    areas = [a["area"] for a in body["areas"]]
    assert areas == [a for a in AREAS if a in areas]
    keys = [s["key"] for a in body["areas"] for s in a["settings"]]
    assert keys == [s.key for s in all_settings(include_internal=False)]
    assert all(s["area"] == a["area"] for a in body["areas"] for s in a["settings"])
    assert body["suggestions"] == []

    limit = _setting(body, "commit_limit")
    assert limit["value"] == 200
    assert limit["source"] == "repo_file"
    assert limit["default"] == 5000
    assert limit["changed"] is True
    assert limit["locked"] is False
    assert limit["writable"] is True
    assert limit["type"] == "int"
    assert limit["minimum"] == 1
    assert limit["accepts"] == "a whole number, at least 1"
    assert limit["applies"]


async def test_get_hides_internal_settings_unless_asked(client: AsyncClient):
    from repowise.core.settings import all_settings

    repo = await create_test_repo(client)
    internal = next(s.key for s in all_settings() if s.internal)

    shown = (await client.get(f"/api/repos/{repo['id']}/settings")).json()
    every = (await client.get(f"/api/repos/{repo['id']}/settings", params={"all": 1})).json()

    assert internal not in {s["key"] for a in shown["areas"] for s in a["settings"]}
    assert _setting(every, internal)["internal"] is True


async def test_get_marks_env_and_user_settings_not_writable(client: AsyncClient):
    repo = await create_test_repo(client)

    body = (await client.get(f"/api/repos/{repo['id']}/settings")).json()

    assert _setting(body, "REPOWISE_OFFLINE")["writable"] is False
    assert _setting(body, "hints_enabled")["writable"] is False


async def test_get_never_sends_a_credential(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("REPOWISE_GITLAB_TOKEN", "glpat-not-a-real-token")
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/settings")

    assert "glpat-not-a-real-token" not in resp.text
    token = _setting(resp.json(), "REPOWISE_GITLAB_TOKEN")
    assert (token["value"], token["source"]) == ("(set, hidden)", "env")


async def test_get_masks_a_url_password(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("REPOWISE_PUBLIC_URL", "https://ci:hunter2@repowise.example/mcp")
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/settings")

    assert "hunter2" not in resp.text
    url = _setting(resp.json(), "REPOWISE_PUBLIC_URL")
    assert url["value"] == "https://***@repowise.example/mcp"


async def test_get_refuses_an_unreadable_config(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "commit_limit: [unclosed\n")

    resp = await client.get(f"/api/repos/{repo['id']}/settings")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "config_unreadable"


async def test_get_unknown_repo_is_404(client: AsyncClient):
    resp = await client.get("/api/repos/nope/settings")
    assert resp.status_code == 404


async def test_patch_sets_a_value_and_reports_the_change(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "commit_limit", "value": 300}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "set"
    assert body["key"] == "commit_limit"
    assert body["changed"] is True
    assert body["before"]["value"] == 5000
    assert body["before"]["source"] == "default"
    assert body["after"]["value"] == 300
    assert body["after"]["source"] == "repo_file"
    assert body["undo"] == ["repowise config reset commit_limit"]
    assert body["effect"]
    assert body["env_override"] is None
    saved = yaml.safe_load(_config_path(repo).read_text(encoding="utf-8"))
    assert saved["commit_limit"] == 300


async def test_patch_matches_the_cli_json(client: AsyncClient, tmp_path: Path):
    """The API and ``repowise config set --format json`` give the same report."""
    import json

    from click.testing import CliRunner

    from repowise.cli.commands.config_cmd import config_group

    repo = await create_test_repo(client)
    _write_config(repo, "commit_limit: 200\n")
    api = (
        await client.patch(
            f"/api/repos/{repo['id']}/settings", json={"key": "commit_limit", "value": 400}
        )
    ).json()

    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    (other / ".repowise").mkdir()
    (other / ".repowise" / "config.yaml").write_text("commit_limit: 200\n", encoding="utf-8")
    result = CliRunner().invoke(
        config_group, ["set", "commit_limit", "400", "--path", str(other), "--format", "json"]
    )
    cli = json.loads(result.output)
    cli.pop("repo")
    cli["undo"] = [line.split(" --path")[0] for line in cli["undo"]]
    for state in (api["before"], api["after"]):
        state.pop("schema_version", None)
    for state in (cli["before"], cli["after"]):
        state.pop("schema_version", None)
    assert api == cli


async def test_patch_moves_neighbours_and_reports_them(client: AsyncClient):
    repo = await create_test_repo(client)

    body = (
        await client.patch(
            f"/api/repos/{repo['id']}/settings", json={"key": "decisions.mode", "value": "off"}
        )
    ).json()

    assert body["after"]["value"] == "off"
    assert body["also"], "turning decisions off moves the switches under it"
    assert all({"key", "before", "after"} <= set(m) for m in body["also"])


async def test_patch_reset_goes_back_to_the_default(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "commit_limit: 200\nwiki_style: concise\n")

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "commit_limit", "reset": True}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "reset"
    assert body["after"]["source"] == "default"
    assert body["after"]["value"] == 5000
    assert body["undo"] == ["repowise config set commit_limit 200"]
    saved = yaml.safe_load(_config_path(repo).read_text(encoding="utf-8"))
    assert "commit_limit" not in saved
    assert saved["wiki_style"] == "concise"


async def test_patch_refuses_an_invalid_value(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "commit_limit", "value": 0}
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_value"
    assert "at least 1" in detail["message"]
    assert detail["accepts"].startswith("a whole number")
    assert not _config_path(repo).exists()


async def test_patch_refuses_an_unknown_key(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "no.such", "value": 1}
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_setting"


async def test_patch_refuses_an_env_only_setting(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "REPOWISE_OFFLINE", "value": True}
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "env_only"
    assert "REPOWISE_OFFLINE" in detail["message"]


async def test_patch_refuses_a_user_setting(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "hints_enabled", "value": False}
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "user_setting"
    assert "repowise config set hints_enabled" in detail["remedy"]


async def test_patch_leaves_an_unreadable_config_alone(client: AsyncClient):
    repo = await create_test_repo(client)
    broken = "commit_limit: [unclosed\n"
    _write_config(repo, broken)

    resp = await client.patch(
        f"/api/repos/{repo['id']}/settings", json={"key": "commit_limit", "value": 10}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "config_unreadable"
    assert _config_path(repo).read_text(encoding="utf-8") == broken


async def test_patch_needs_exactly_one_of_value_and_reset(client: AsyncClient):
    repo = await create_test_repo(client)
    url = f"/api/repos/{repo['id']}/settings"

    neither = await client.patch(url, json={"key": "commit_limit"})
    both = await client.patch(url, json={"key": "commit_limit", "value": 3, "reset": True})

    assert neither.status_code == 422
    assert both.status_code == 422
