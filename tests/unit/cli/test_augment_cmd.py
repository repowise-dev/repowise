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
        json.dumps(
            {
                "last_sync_commit": "1111111111111111111111111111111111111111",
                "docs_mode": "llm",
            }
        ),
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


def test_claude_session_start_emits_freshness_context(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repowise_repo(tmp_path)
    # The dedup marker is keyed on (event, context); identical blocks across
    # fast test runs would suppress the emission, so bypass it here.
    monkeypatch.setattr(
        "repowise.cli.commands.augment_cmd.command._claim_emission", lambda e, c: True
    )

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cwd": str(tmp_path),
        },
    )

    assert result.exit_code == 0
    # tmp_path is not a git repo, so the handler emits the line without the
    # freshness fact, never a false "current" claim.
    context = _hook_context(result.output)
    assert "local codebase index" in context
    assert "current" not in context


def test_claude_user_prompt_submit_stays_silent(runner: CliRunner, tmp_path: Path) -> None:
    _init_repowise_repo(tmp_path)

    result = _invoke_augment(
        runner,
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Where is the auth flow?",
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


def test_claim_emission_cleans_up_expired_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from repowise.cli.commands.augment_cmd.command import _claim_emission

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    active = tmp_path / ".repowise-augment-active"
    expired = tmp_path / ".repowise-augment-expired"
    active.write_text("active", encoding="utf-8")
    expired.write_text("expired", encoding="utf-8")

    now = time.time()
    os.utime(str(expired), (now - 20, now - 20))
    os.utime(str(active), (now, now))

    claimed = _claim_emission("test_event", "test_context")
    assert claimed is True

    assert not expired.exists()
    assert active.exists()


def test_claim_emission_cleanup_is_throttled_by_timestamp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from repowise.cli.commands.augment_cmd.command import _SWEEP_THROTTLE_FILE, _claim_emission

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    # First run creates the throttle stamp
    claimed1 = _claim_emission("event_1", "ctx_1")
    assert claimed1 is True
    stamp_path = tmp_path / _SWEEP_THROTTLE_FILE
    assert stamp_path.exists()

    # Create an expired marker while throttle stamp is fresh
    expired_during_throttle = tmp_path / ".repowise-augment-expired-during-throttle"
    expired_during_throttle.write_text("old", encoding="utf-8")
    now = time.time()
    os.utime(str(expired_during_throttle), (now - 20, now - 20))

    # Second run is within TTL window, so sweep is throttled and does not delete it
    claimed2 = _claim_emission("event_2", "ctx_2")
    assert claimed2 is True
    assert expired_during_throttle.exists()

    # Age the throttle stamp beyond the TTL window
    os.utime(str(stamp_path), (now - 20, now - 20))

    # Third run performs the sweep and unlinks the expired marker
    claimed3 = _claim_emission("event_3", "ctx_3")
    assert claimed3 is True
    assert not expired_during_throttle.exists()


def test_cleanup_stale_markers_direct(tmp_path: Path) -> None:
    import os
    import time

    from repowise.cli.commands.augment_cmd.command import _cleanup_stale_markers

    active = tmp_path / ".repowise-augment-active"
    expired = tmp_path / ".repowise-augment-expired"
    active.write_text("active", encoding="utf-8")
    expired.write_text("expired", encoding="utf-8")

    now = time.time()
    os.utime(str(expired), (now - 20, now - 20))
    os.utime(str(active), (now, now))

    _cleanup_stale_markers(tmp_path, now)

    assert not expired.exists()
    assert active.exists()


def test_claim_emission_takeover_stale_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import os
    import time

    from repowise.cli.commands.augment_cmd.command import _claim_emission

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    event = "ev"
    ctx = "ctx"
    key = hashlib.sha1(f"{event}\x00{ctx}".encode(), usedforsecurity=False).hexdigest()[:16]
    marker = tmp_path / f".repowise-augment-{key}"
    marker.write_text("existing", encoding="utf-8")

    now = time.time()
    # Marker is recent -> claim returns False
    os.utime(str(marker), (now, now))
    assert _claim_emission(event, ctx) is False

    # Marker is stale -> claim takes it over and returns True
    os.utime(str(marker), (now - 20, now - 20))
    assert _claim_emission(event, ctx) is True
