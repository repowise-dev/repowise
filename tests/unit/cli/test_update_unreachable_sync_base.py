"""Regression tests for ``repowise update`` when ``last_sync_commit`` is unreachable (#5228)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd import command as update_cmd
from repowise.cli.helpers import load_state, read_update_lock, save_state
from repowise.cli.main import cli
from repowise.core.ingestion.change_detector import ChangeDetector
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY

CURRENT_GIT_HISTORY = {GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION}
MISSING_SHA = "0" * 40


def _split_output_runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    """Two-commit git repository returning ``(repo, c0, c1)``."""
    import git as gitpython

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    r = gitpython.Repo.init(repo)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")

    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    r.index.add(["a.py"])
    c0 = r.index.commit("c0").hexsha
    c1 = r.index.commit("c1").hexsha
    r.close()

    (repo / ".repowise").mkdir()
    return repo, c0, c1


def test_update_with_unknown_sync_commit_exits_nonzero_and_preserves_state(
    git_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When last_sync_commit is not in git history, update fails with a reasoned
    error naming recovery commands and leaves last_sync_commit untouched."""
    from repowise.cli.platform import telemetry

    repo, _c0, _c1 = git_repo
    recorded: dict = {}
    monkeypatch.setattr(telemetry, "add_command_outcome", recorded.update)
    save_state(
        repo,
        {"last_sync_commit": MISSING_SHA, "docs_mode": "none", **CURRENT_GIT_HISTORY},
    )

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])

    assert result.exit_code != 0
    assert load_state(repo)["last_sync_commit"] == MISSING_SHA
    assert "repowise update --since <reachable ref>" in (result.output or "")
    assert "repowise init" in (result.output or "")
    assert recorded.get("failure_reason") == "missing_sync_base"


def test_update_with_unknown_sync_commit_emits_json_error(
    git_repo: tuple[Path, str, str],
) -> None:
    repo, _c0, _c1 = git_repo
    save_state(
        repo,
        {"last_sync_commit": MISSING_SHA, "docs_mode": "none", **CURRENT_GIT_HISTORY},
    )

    result = _split_output_runner().invoke(
        cli, ["update", str(repo), "--no-workspace", "--progress", "json"]
    )

    assert result.exit_code != 0
    assert load_state(repo)["last_sync_commit"] == MISSING_SHA
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [e["event"] for e in events] == ["start", "error"]
    assert "repowise update --since <reachable ref>" in events[1]["message"]
    assert "repowise init" in events[1]["message"]


def test_update_since_reachable_commit_recovers_from_unknown_sync_commit(
    git_repo: tuple[Path, str, str],
) -> None:
    """Passing --since <reachable ref> overrides the missing last_sync_commit."""
    repo, c0, c1 = git_repo
    save_state(
        repo,
        {"last_sync_commit": MISSING_SHA, "docs_mode": "none", **CURRENT_GIT_HISTORY},
    )

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--no-workspace", "--since", c0]
    )

    assert result.exit_code == 0, result.output
    assert load_state(repo)["last_sync_commit"] == c1


def test_update_git_diff_failure_does_not_advance_sync_commit(
    git_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Other git failures during diffing do not read as 'no changes' or move last_sync_commit."""
    from repowise.cli.platform import telemetry

    repo, c0, _c1 = git_repo
    recorded: dict = {}
    monkeypatch.setattr(telemetry, "add_command_outcome", recorded.update)
    save_state(
        repo,
        {"last_sync_commit": c0, "docs_mode": "none", **CURRENT_GIT_HISTORY},
    )

    def _boom(_self, _base_ref="HEAD~1", _until_ref="HEAD"):
        raise RuntimeError("corrupt packfile")

    monkeypatch.setattr(ChangeDetector, "get_changed_files", _boom)

    result = _split_output_runner().invoke(
        cli, ["update", str(repo), "--no-workspace", "--progress", "json"]
    )

    assert result.exit_code != 0
    assert load_state(repo)["last_sync_commit"] == c0
    assert recorded.get("failure_reason") == "git_diff_failed"
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[-1]["event"] == "error"
    assert "corrupt packfile" in events[-1]["message"]
    assert read_update_lock(repo) is None
    assert callable(update_cmd._verify_sync_base)
