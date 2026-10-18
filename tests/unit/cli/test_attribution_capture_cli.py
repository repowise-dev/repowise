"""Edit capture on the CLI: status per agent, purge, the doctor notes check, Cursor's hook."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.attribution_cmd import attribution_group
from repowise.cli.commands.doctor_cmd.repo_checks import _attribution_notes_check
from repowise.core import attribution
from repowise.core.edit_capture.binder import bind_pending
from repowise.core.edit_capture.edits import CapturedCall, FileEdit
from repowise.core.edit_capture.store import record_call, sidecar_dir

BODY = [
    "def settle(ledger):",
    "    balance = sum(entry.amount for entry in ledger)",
    "    fees = compute_fees(balance, ledger.currency)",
    "    return balance - fees",
]


@pytest.fixture
def user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home" / ".repowise" / "config.yaml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(attribution, "user_config_path", lambda: path)
    for var in ("DO_NOT_TRACK", "REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    return path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, user_config: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    (root / ".repowise").mkdir(parents=True)
    _git(root, "init", "-q")
    for key, value in (("user.email", "d@example.com"), ("user.name", "D"), ("commit.gpgsign", "false")):
        _git(root, "config", key, value)
    (root / ".gitignore").write_text(".repowise/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _opt_in(root: Path, user_config: Path, level: str, agents: str = "[claude-code]") -> None:
    cfg = f"attribution:\n  capture: {level}\n  agents: {agents}\n"
    (root / ".repowise" / "config.yaml").write_text(cfg, encoding="utf-8")
    user_config.write_text(cfg, encoding="utf-8")


def test_status_shows_capture_per_agent(repo: Path, user_config: Path) -> None:
    _opt_in(repo, user_config, "local")
    result = CliRunner().invoke(attribution_group, ["status", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    by_agent = json.loads(result.output)["capture_by_agent"]
    assert by_agent["claude-code"] == "local"
    assert by_agent["codex"] == "off (not in attribution.agents)"
    assert by_agent["vscode"].startswith("no capture adapter")

    text = CliRunner().invoke(attribution_group, ["status", str(repo)]).output
    assert "capture  claude-code" in text


def test_purge_deletes_sidecar_and_our_notes_only(repo: Path, user_config: Path) -> None:
    _opt_in(repo, user_config, "notes")
    (repo / "other.py").write_text("print('x')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "other")
    other = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "notes", "--ref=ai", "add", "-m", "foreign\n---\n{}", other)

    call = CapturedCall(tool="Edit", edits=[FileEdit("a.py", BODY)], session_id="s", cwd=str(repo))
    record_call(repo, "claude_code", call)
    (repo / "a.py").write_text("\n".join(BODY) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "agent")
    ours = _git(repo, "rev-parse", "HEAD").strip()
    assert len(bind_pending(repo, capture="notes")) == 1

    result = CliRunner().invoke(attribution_group, ["purge", str(repo), "--yes"])
    assert result.exit_code == 0, result.output
    assert not sidecar_dir(repo).exists()
    listing = _git(repo, "notes", "--ref=ai", "list")
    assert other in listing and ours not in listing
    assert "1 git-ai note(s)" in result.output


def test_doctor_notes_check_only_at_notes_level(repo: Path, user_config: Path) -> None:
    _opt_in(repo, user_config, "local")
    assert _attribution_notes_check(repo) is None

    _opt_in(repo, user_config, "notes")
    no_remote = _attribution_notes_check(repo)
    assert no_remote is not None and no_remote.ok and "no origin remote" in no_remote.detail

    _git(repo, "remote", "add", "origin", "https://example.invalid/demo.git")
    missing = _attribution_notes_check(repo)
    assert missing is not None and not missing.ok
    assert "remote.origin.fetch" in missing.detail and "notes.rewriteRef" in missing.detail

    _git(repo, "config", "--add", "remote.origin.fetch", "+refs/notes/ai:refs/notes/ai")
    _git(repo, "config", "--add", "notes.rewriteRef", "refs/notes/ai")
    fine = _attribution_notes_check(repo)
    assert fine is not None and fine.ok and "git push origin refs/notes/ai" in fine.detail


def test_cursor_capture_hook_needs_the_user_to_opt_in(
    user_config: Path, cursor_hooks_json: Path
) -> None:
    from repowise.cli.agent_targets.registry import get_target
    from repowise.cli.agent_targets.types import Scope

    target = get_target("cursor")
    target.install(Scope.USER)
    hooks = json.loads(cursor_hooks_json.read_text(encoding="utf-8"))["hooks"]
    assert "afterFileEdit" not in hooks and "preToolUse" in hooks

    user_config.write_text(
        "attribution:\n  capture: local\n  agents: [cursor]\n", encoding="utf-8"
    )
    target.install(Scope.USER)
    target.install(Scope.USER)
    hooks = json.loads(cursor_hooks_json.read_text(encoding="utf-8"))["hooks"]
    assert hooks["afterFileEdit"] == [{"command": "repowise augment --client cursor", "timeout": 5}]

    target.uninstall(Scope.USER)
    assert not cursor_hooks_json.exists()


def test_hook_stdout_stays_json_with_an_invalid_config(repo: Path, user_config: Path) -> None:
    """A config warning goes to stderr; stdout is the agent's JSON channel."""
    import os
    import sys

    _opt_in(repo, user_config, "everything")
    target = repo / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    payload = {
        "session_id": "s1",
        "cwd": str(repo),
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "old_string": "x = 1", "new_string": "x = 2"},
        "tool_response": {"filePath": str(target), "structuredPatch": []},
    }
    env = dict(os.environ)
    env["HOME"] = env["USERPROFILE"] = str(user_config.parent.parent)
    out = subprocess.run(
        [sys.executable, "-c", "from repowise.cli.augment_hook import main; main()"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
    )
    assert out.returncode == 0
    assert "attribution_config_invalid" in out.stderr  # the warning did fire
    if out.stdout.strip():
        json.loads(out.stdout)


def _claude_write(home: Path, cwd: Path, rel: str, lines: list[str]) -> None:
    """One Claude Code ``Write`` of a new file, as the transcript records it."""
    import time

    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 600))
    base = {"sessionId": "s", "cwd": str(cwd), "timestamp": ts}
    use = base | {"type": "assistant", "message": {"model": "claude-opus-5", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Write",
         "input": {"file_path": str(cwd / rel), "content": "\n".join(lines)}}]}}
    result = base | {"type": "user", "toolUseResult": {"type": "create"}, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}
    path = home / ".claude" / "projects" / "p" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(use) + "\n" + json.dumps(result) + "\n", encoding="utf-8")


def test_backfill_refuses_without_consent(repo: Path, user_config: Path) -> None:
    result = CliRunner().invoke(attribution_group, ["backfill", str(repo), "--agent", "claude-code"])
    assert result.exit_code != 0
    assert "attribution.capture is off" in result.output


def test_backfill_reports_and_status_shows_the_run(
    repo: Path, user_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "fakehome"
    monkeypatch.setattr(Path, "home", lambda: home)
    _opt_in(repo, user_config, "local", "[claude-code, codex]")
    lines = [f"value_{i} = compute_value({i}, scale)" for i in range(8)]
    _claude_write(home, repo, "src/values.py", lines)
    (repo / "src").mkdir()
    (repo / "src" / "values.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "values")
    state = repo / ".repowise" / "state.json"
    state.write_text(json.dumps({"git_history_version": 3, "last_sync_commit": "abc"}), encoding="utf-8")

    result = CliRunner().invoke(
        attribution_group, ["backfill", str(repo), "--agent", "claude-code,codex", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["bound"] == 1 and report["commits_scanned"] >= 1
    assert report["covered_after"] == report["covered_before"] + 1
    # The next update re-reads git history once; nothing else in the state moves.
    assert report["history_rewalk_on_next_update"] is True
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "git_history_version": "attribution-backfill",
        "last_sync_commit": "abc",
    }

    status = CliRunner().invoke(attribution_group, ["status", str(repo), "--format", "json"])
    assert json.loads(status.output)["backfill"]["bound"] == 1
    text = CliRunner().invoke(attribution_group, ["status", str(repo)]).output
    assert "backfill" in text and "1 bound" in text
