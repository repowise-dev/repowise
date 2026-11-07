"""Pi adapter: tool names, PreToolUse parsing, response rendering, install probe."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from repowise.cli.agent_adapters import adapter_for
from repowise.cli.agent_adapters.pi import (
    EDIT_TOOL_NAMES,
    READ_TOOL_NAMES,
    SEARCH_TOOL_NAMES,
    SHELL_TOOL_NAMES,
    PiAdapter,
    extension_path,
)


def _payload(tool_name: str, command: str, **extra: object) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "cwd": "/repo",
            "session_id": "s1",
            **extra,
        }
    )


def test_registry_resolves_pi() -> None:
    adapter = adapter_for("pi")
    assert isinstance(adapter, PiAdapter)
    assert adapter.name == "pi"
    assert adapter_for("Pi").name == "pi"


def test_tool_names_are_pi_spellings() -> None:
    assert {"bash", "powershell"} == SHELL_TOOL_NAMES
    assert {"read"} == READ_TOOL_NAMES
    assert {"edit", "write"} == EDIT_TOOL_NAMES
    assert {"grep", "find"} == SEARCH_TOOL_NAMES
    adapter = PiAdapter()
    assert adapter.shell_tool_names == SHELL_TOOL_NAMES
    assert adapter.read_tool_names == READ_TOOL_NAMES
    assert adapter.edit_tool_names == EDIT_TOOL_NAMES
    assert adapter.search_tool_names == SEARCH_TOOL_NAMES
    # `ls` lists a directory: no surface wants it, so it is in no set.
    assert "ls" not in (
        SHELL_TOOL_NAMES | READ_TOOL_NAMES | EDIT_TOOL_NAMES | SEARCH_TOOL_NAMES
    )


def test_parse_hook_payload_bash() -> None:
    request = PiAdapter().parse_hook_payload(_payload("bash", "pytest -q"))
    assert request is not None
    assert request.command == "pytest -q"
    assert request.cwd == "/repo"
    assert request.session_id == "s1"
    assert request.shell == "posix"


def test_parse_hook_payload_powershell_is_powershell() -> None:
    request = PiAdapter().parse_hook_payload(_payload("powershell", "pytest -q"))
    assert request is not None
    assert request.shell == "powershell"


def test_parse_hook_payload_declines() -> None:
    adapter = PiAdapter()
    assert adapter.parse_hook_payload("not json") is None
    assert adapter.parse_hook_payload(json.dumps({"hook_event_name": "PostToolUse"})) is None
    assert adapter.parse_hook_payload(_payload("read", "pytest -q")) is None
    assert adapter.parse_hook_payload(_payload("bash", "   ")) is None
    assert adapter.parse_hook_payload(_payload("Bash", "pytest -q")) is None


def test_render_response_is_the_claude_shape() -> None:
    from repowise.cli.agent_adapters.base import RewriteResult

    rendered = json.loads(
        PiAdapter().render_response(
            RewriteResult("repowise distill pytest -q", "allow", "distilled")
        )
    )
    specific = rendered["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "allow"
    assert specific["updatedInput"] == {"command": "repowise distill pytest -q"}


def test_rewrite_posture_and_replacement() -> None:
    adapter = PiAdapter()
    # Pi mutates `event.input` directly: no ask-with-mutation, so `ask`
    # decisions pass through untouched — the Codex posture.
    assert adapter.rewrite_permissions == {"allow"}
    # Pi's `tool_result` handlers can return a content patch.
    assert adapter.replaces_tool_output is True
    assert adapter.supports_updated_output() is True
    assert adapter.savings_source == "hook-pi"


def test_detect_by_executable_or_agent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert PiAdapter().detect() is False
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    assert PiAdapter().detect() is True
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pi")
    assert PiAdapter().detect() is True


def test_extension_probe_install_and_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    adapter = PiAdapter()
    assert adapter.rewrite_hook_installed() is False
    assert adapter.rewrite_hook_matcher() is None
    # Install is owned by the agent-target installer, not the adapter.
    assert adapter.install_rewrite_hook() is None
    path = extension_path()
    path.parent.mkdir(parents=True)
    path.write_text("// repowise", encoding="utf-8")
    assert adapter.rewrite_hook_installed() is True
    assert adapter.uninstall_rewrite_hook() is True
    assert not path.exists()
    assert adapter.uninstall_rewrite_hook() is False


def test_extension_dir_follows_pi_coding_agent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "custom-pi"))
    assert str(extension_path()).startswith(str(tmp_path / "custom-pi"))
    assert extension_path().name == "repowise.ts"
    assert os.path.basename(str(extension_path())) == "repowise.ts"
