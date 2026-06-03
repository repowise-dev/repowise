from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke_augment(
    runner: CliRunner,
    payload: dict[str, Any],
    args: list[str] | None = None,
):
    return runner.invoke(cli, ["augment", *(args or [])], input=json.dumps(payload))


def _hook_context(output: str) -> str:
    response = json.loads(output)
    return response["hookSpecificOutput"]["additionalContext"]


def _init_repowise_repo(path: Path) -> None:
    repowise_dir = path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "state.json").write_text(
        json.dumps({"last_sync_commit": "1111111111111111111111111111111111111111"}),
        encoding="utf-8",
    )


def test_codex_session_start_payload_returns_mcp_context(runner: CliRunner, tmp_path: Path) -> None:
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cwd": str(tmp_path),
        },
        ["--client", "codex"],
    )

    assert result.exit_code == 0
    assert "repowise MCP tools" in _hook_context(result.output)


def test_codex_user_prompt_submit_payload_returns_mcp_context(
    runner: CliRunner, tmp_path: Path
) -> None:
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "turn-1",
            "prompt": "Where is the auth flow?",
            "cwd": str(tmp_path),
        },
        ["--client", "codex"],
    )

    assert result.exit_code == 0
    assert "semantic search" in _hook_context(result.output)


def test_claude_lifecycle_payload_stays_silent_without_codex_client(
    runner: CliRunner, tmp_path: Path
) -> None:
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cwd": str(tmp_path),
        },
    )

    assert result.exit_code == 0
    assert result.output == ""


def test_codex_post_tool_use_bash_detects_stale_wiki(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repowise_repo(tmp_path)

    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args == ["git", "rev-parse", "HEAD"]
        return subprocess.CompletedProcess(
            args, 0, stdout="2222222222222222222222222222222222222222\n", stderr=""
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m hook-test"},
            "tool_response": {"exit_code": 0, "stdout": "[main abc123] hook-test"},
            "cwd": str(tmp_path),
        },
    )

    assert result.exit_code == 0
    context = _hook_context(result.output)
    assert "Wiki is stale" in context
    assert "11111111" in context
    assert "22222222" in context


def test_codex_post_tool_use_apply_patch_flags_stale_context(
    runner: CliRunner, tmp_path: Path
) -> None:
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch"},
            "tool_response": {"success": True},
            "cwd": str(tmp_path),
        },
        ["--client", "codex"],
    )

    assert result.exit_code == 0
    assert "Files were edited" in _hook_context(result.output)


def test_edit_post_tool_use_stays_silent_without_codex_client(
    runner: CliRunner, tmp_path: Path
) -> None:
    # The edit freshness notice is a Codex-only lifecycle hook; an Edit PostToolUse
    # delivered by an existing Claude Code augment install (no --client) must not emit
    # a Codex-flavored banner.
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "a.py"},
            "tool_response": {"success": True},
            "cwd": str(tmp_path),
        },
    )

    assert result.exit_code == 0
    assert result.output == ""
