"""``repowise init --yes`` installs the agent hooks by default, and only then.

Every run points the home directory at a temp dir, so nothing here can touch
the developer's real Claude Code, Claude Desktop or Codex config.
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml
from click.testing import CliRunner

from repowise.cli.main import cli

_HEADLESS = ("--index-only", "--no-hook", "--no-codex", "--no-workspace", "-y")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)  # Claude Code counts as installed
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # The suite turns editor setup off for every test; these tests are about it.
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    return home


@pytest.fixture
def repo(tmp_path, sample_repo_path, monkeypatch):
    dest = tmp_path / "repo"
    shutil.copytree(sample_repo_path, dest)
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    return dest


def _config(repo) -> dict:
    path = repo / ".repowise" / "config.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _home_files(home) -> set[str]:
    return {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()}


def test_no_editor_setup_installs_no_hook_and_writes_no_user_config(fake_home, repo) -> None:
    result = CliRunner().invoke(
        cli, ["init", str(repo), "--no-editor-setup", *_HEADLESS], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    # repowise's own state dirs are fine; any agent or editor config is not.
    written = {f for f in _home_files(fake_home) if not f.startswith((".repowise/", ".config/repowise/"))}
    assert written == set()
    cfg = _config(repo)
    assert "hooks" not in cfg
    assert "commands" not in (cfg.get("distill") or {})


def test_yes_installs_the_rewrite_hook_and_every_surface(fake_home, repo) -> None:
    result = CliRunner().invoke(cli, ["init", str(repo), *_HEADLESS], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    settings = json.loads((fake_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("repowise-rewrite" in c for c in commands)
    cfg = _config(repo)
    assert cfg["distill"]["commands"]["enabled"] is True
    assert cfg["hooks"] == {"read_skeleton": True, "search_digest": True, "read_reread": True}
    assert "--no-distill-hook" in result.output


def test_a_repo_that_opted_out_stays_off(fake_home, repo) -> None:
    runner = CliRunner()
    first = runner.invoke(
        cli, ["init", str(repo), "--no-distill-hook", *_HEADLESS], catch_exceptions=False
    )
    assert first.exit_code == 0, first.output
    again = runner.invoke(
        cli, ["init", str(repo), "--force", *_HEADLESS], catch_exceptions=False
    )
    assert again.exit_code == 0, again.output

    settings_path = fake_home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    assert not settings.get("hooks", {}).get("PreToolUse")
    cfg = _config(repo)
    assert cfg["distill"]["commands"]["enabled"] is False
    assert set(cfg["hooks"].values()) == {False}
