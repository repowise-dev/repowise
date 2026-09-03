"""GET/PUT ``/decisions/settings``: the same policy the CLI resolves."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from httpx import AsyncClient

from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def _stable_provider(monkeypatch):
    """Status must not depend on which API keys the test machine carries."""
    monkeypatch.setattr(
        "repowise.server.routers.decisions._provider_available", lambda _p: True
    )


def _config(repo: dict) -> dict:
    path = Path(repo["local_path"]) / ".repowise" / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_config(repo: dict, text: str) -> None:
    cfg_dir = Path(repo["local_path"]) / ".repowise"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(text, encoding="utf-8")


async def test_get_returns_the_full_source_registry(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/settings")

    assert resp.status_code == 200
    body = resp.json()
    assert [s["key"] for s in body["sources"]] == [
        "inline_marker",
        "git_archaeology",
        "adr",
        "pr",
        "comment",
        "session",
        "session_discovery",
        "cli",
    ]
    assert body["preset"] == "default"
    assert body["enabled"] is True
    assert body["etag"]


async def test_settings_path_is_not_shadowed_by_the_decision_id_route(client: AsyncClient):
    """``settings`` must not resolve as a decision id."""
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/settings")

    assert resp.status_code == 200
    assert "sources" in resp.json()


async def test_get_reports_a_legacy_config_without_changing_it(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "decisions:\n  session_mining: false\n")

    body = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()

    session = next(s for s in body["sources"] if s["key"] == "session")
    assert session["enabled"] is False
    assert _config(repo)["decisions"] == {"session_mining": False}


async def test_put_applies_a_partial_change(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings", json={"llm": False}
    )

    assert resp.status_code == 200
    assert resp.json()["llm"] is False
    assert _config(repo)["decisions"]["llm"] is False


async def test_put_preset_then_source_override_in_one_call(client: AsyncClient):
    repo = await create_test_repo(client)

    body = (
        await client.put(
            f"/api/repos/{repo['id']}/decisions/settings",
            json={"preset": "balanced", "sources": {"comment": {"enabled": True}}},
        )
    ).json()

    by_key = {s["key"]: s for s in body["sources"]}
    assert by_key["comment"]["enabled"] is True
    assert by_key["session"]["enabled"] is True
    assert body["preset"] == "custom"


async def test_put_preserves_unrelated_config_keys(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "provider: anthropic\ncoverage:\n  path: cov.xml\n")

    await client.put(f"/api/repos/{repo['id']}/decisions/settings", json={"enabled": False})

    cfg = _config(repo)
    assert cfg["provider"] == "anthropic"
    assert cfg["coverage"] == {"path": "cov.xml"}


async def test_a_stale_etag_conflicts(client: AsyncClient):
    repo = await create_test_repo(client)
    stale = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()["etag"]
    await client.put(f"/api/repos/{repo['id']}/decisions/settings", json={"llm": False})

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"enabled": False, "etag": stale},
    )

    assert resp.status_code == 409
    assert _config(repo)["decisions"]["enabled"] is True


async def test_a_current_etag_writes(client: AsyncClient):
    repo = await create_test_repo(client)
    etag = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()["etag"]

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings", json={"llm": False, "etag": etag}
    )

    assert resp.status_code == 200


async def test_an_unknown_preset_is_a_400(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings", json={"preset": "aggressive"}
    )

    assert resp.status_code == 400


async def test_an_unknown_source_is_a_400(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"sources": {"code_comment": {"enabled": False}}},
    )

    assert resp.status_code == 400


async def test_manual_entry_cannot_be_switched_off(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"sources": {"cli": {"enabled": False}}},
    )

    assert resp.status_code == 400


async def test_an_unknown_repo_is_a_404(client: AsyncClient):
    resp = await client.get("/api/repos/does-not-exist/decisions/settings")

    assert resp.status_code == 404


async def test_the_api_and_the_cli_resolve_the_same_policy(client: AsyncClient):
    """The registry drift guard: one resolver, two surfaces."""
    from repowise.core.analysis.decisions.policy_store import load_policy

    repo = await create_test_repo(client)
    await client.put(
        f"/api/repos/{repo['id']}/decisions/settings", json={"preset": "local_only"}
    )

    body = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()
    policy = load_policy(Path(repo["local_path"])).policy

    assert body["preset"] == policy.preset_name()
    assert body["llm"] == policy.llm
    assert {s["key"]: s["enabled"] for s in body["sources"]} == {
        s["key"]: policy.source_enabled(s["key"]) for s in body["sources"]
    }


async def test_a_misspelt_source_key_is_rejected_not_silently_ignored(client: AsyncClient):
    """A UI toggle that returns 200 having changed nothing reads as saved."""
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"sources": {"comment": {"enable": False}}},
    )

    assert resp.status_code == 422


async def test_an_unparseable_config_is_a_400(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "decisions:\n\tsources: {}\n")

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/settings")

    assert resp.status_code == 400
    assert "Could not parse" in resp.json()["detail"]


async def test_legacy_keys_reach_a_settings_client(client: AsyncClient):
    repo = await create_test_repo(client)
    _write_config(repo, "decisions:\n  session_mining: false\n")

    body = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()

    assert body["legacy_keys"] == ["session_mining"]


async def test_a_repo_with_no_checkout_on_this_server_is_a_404(client: AsyncClient, tmp_path):
    import shutil

    repo = await create_test_repo(client)
    shutil.rmtree(repo["local_path"])

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/settings")

    assert resp.status_code == 404
    assert "not accessible" in resp.json()["detail"]


async def test_put_discovery_merges_rather_than_replacing(client: AsyncClient):
    """A UI sending one budget field must not reset the other to its default."""
    repo = await create_test_repo(client)
    await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"discovery": {"max_sessions": 4, "max_input_tokens": 50000}},
    )

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"discovery": {"max_sessions": 6}},
    )

    assert resp.status_code == 200
    assert resp.json()["discovery"] == {"max_sessions": 6, "max_input_tokens": 50000}


async def test_put_preset_keeps_a_custom_discovery_budget(client: AsyncClient):
    """A preset names source membership; the budget is not part of it."""
    repo = await create_test_repo(client)
    await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"discovery": {"max_sessions": 4}},
    )

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings", json={"preset": "balanced"}
    )

    assert resp.status_code == 200
    assert resp.json()["discovery"]["max_sessions"] == 4


async def test_put_rejects_an_out_of_range_budget(client: AsyncClient):
    repo = await create_test_repo(client)

    resp = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"discovery": {"max_sessions": 999}},
    )

    assert resp.status_code == 422
