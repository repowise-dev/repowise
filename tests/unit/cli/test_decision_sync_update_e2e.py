"""Decision sync through a real ``repowise update``.

A user with no hosted account must see nothing about it, and a user who
turned it on must never have an update fail because of it.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.cli.platform import credentials, store
from repowise.cli.platform.client import PlatformClient

from .test_decision_sync import HOSTED, _pulled

# Words that would tell a user without an account that sync or hosted exists.
_HOSTED_WORDS = ("repowise.dev", "hosted", "decision sync", "synced", "sync:", "push", "pull")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.delenv("REPOWISE_OFFLINE", raising=False)
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "remote", "add", "origin", "git@github.com:acme/widget.git")
    (path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial")

    from repowise.cli.helpers import save_state
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(path))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(path), capture_output=True, text=True
    ).stdout.strip()
    save_state(path, {"last_sync_commit": head, "docs_enabled": False})
    return path


def _commit_change(repo: Path) -> None:
    (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", "b.py")
    _git(repo, "commit", "-q", "-m", "add b")


def _update(repo: Path):
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    return result.output


def test_without_an_account_nothing_mentions_sync(repo: Path, monkeypatch) -> None:
    def boom(*_a, **_k):
        pytest.fail("the network was used")

    for verb in ("get_json", "post_json", "get", "post", "post_status"):
        monkeypatch.setattr(PlatformClient, verb, boom)

    _commit_change(repo)
    outputs = [_update(repo), _update(repo)]
    for args in (
        ["status", str(repo)],
        ["decision", "status", str(repo)],
        ["decision", "list", str(repo)],
        ["decision", "health", str(repo)],
        ["decision", "config", "show", str(repo)],
    ):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    for output in outputs:
        lowered = output.lower()
        assert not [w for w in _HOSTED_WORDS if w in lowered], output
    assert not (repo / ".repowise" / "decision_sync.json").exists()
    assert not (repo / ".repowise" / "decisions.yaml").exists()


def _signed_in_with_sync(repo: Path) -> None:
    credentials.save(
        {
            "token_kind": "oauth",
            "client_id": "repowise-cli",
            "access_token": "at-1",
            "access_expires_at": int(time.time()) + 3600,
            "refresh_token": "rt-1",
        }
    )
    (repo / ".repowise" / "config.yaml").write_text(
        "decisions:\n  sync: true\n", encoding="utf-8"
    )


def test_a_network_failure_never_fails_the_update(repo: Path, monkeypatch) -> None:
    _signed_in_with_sync(repo)
    monkeypatch.setattr(PlatformClient, "get_json", lambda *a, **k: (0, {}))
    monkeypatch.setattr(PlatformClient, "post_json", lambda *a, **k: (0, {}))

    _commit_change(repo)
    output = _update(repo)

    assert output.count("Couldn't reach repowise.dev") == 1


def test_a_pulled_decision_reaches_the_committed_file_in_the_same_update(
    repo: Path, monkeypatch
) -> None:
    export = CliRunner().invoke(cli, ["decision", "export", str(repo), "--force"])
    assert export.exit_code == 0, export.output
    assert (repo / ".repowise" / "decisions.yaml").exists()
    _signed_in_with_sync(repo)

    def fake_get(_self, path, *, params=None, timeout=None):
        if path == "repos/acme/widget/latest":
            return 200, {"short_id": "snap1", "status": "ready"}
        return 200, {"index_read": True, "decisions": [_pulled()], "total": 1}

    monkeypatch.setattr(PlatformClient, "get_json", fake_get)
    monkeypatch.setattr(PlatformClient, "post_json", lambda *a, **k: pytest.fail("posted"))

    _commit_change(repo)
    output = _update(repo)

    assert "received 1" in output
    data = yaml.safe_load((repo / ".repowise" / "decisions.yaml").read_text(encoding="utf-8"))
    assert [e["id"] for e in data["decisions"]] == [HOSTED]
