"""Cursor hook adapter: ``preToolUse`` payloads, responses and ``hooks.json``.

Payloads are synthetic, shaped after https://cursor.com/docs/agent/hooks.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from repowise.cli import rewrite_hook
from repowise.cli.agent_adapters import adapter_for
from repowise.cli.agent_adapters import cursor as cursor_module
from repowise.cli.agent_adapters.base import SHELL_POSIX, SHELL_POWERSHELL, RewriteResult
from repowise.cli.agent_adapters.cursor import (
    CursorAdapter,
    cursor_rewrite_hook_matcher,
    install_cursor_rewrite_hook,
    uninstall_cursor_rewrite_hook,
)


def _payload(command: str = "pytest -x", **overrides) -> dict:
    base = {
        "conversation_id": "conv-1",
        "generation_id": "gen-1",
        "model": "some-model",
        "hook_event_name": "preToolUse",
        "cursor_version": "2.0.0",
        "workspace_roots": ["/work/root"],
        "user_email": None,
        "transcript_path": None,
        "tool_name": "Shell",
        "tool_input": {"command": command, "working_directory": "/work/repo"},
        "tool_use_id": "abc123",
        "cwd": "/work/cwd",
    }
    base.update(overrides)
    return base


@pytest.fixture
def adapter() -> CursorAdapter:
    return CursorAdapter()


class TestParsePayload:
    def test_shell_pre_tool_use(self, adapter, monkeypatch) -> None:
        monkeypatch.setattr(cursor_module.os, "name", "posix")
        request = adapter.parse_hook_payload(json.dumps(_payload()))
        assert request is not None
        assert request.command == "pytest -x"
        assert request.cwd == "/work/repo"
        assert request.session_id == "conv-1"
        assert request.shell == SHELL_POSIX

    def test_windows_is_powershell(self, adapter, monkeypatch) -> None:
        monkeypatch.setattr(cursor_module.os, "name", "nt")
        request = adapter.parse_hook_payload(json.dumps(_payload()))
        assert request is not None and request.shell == SHELL_POWERSHELL

    def test_cwd_falls_back_to_cwd_then_workspace_root(self, adapter) -> None:
        no_wd = _payload(tool_input={"command": "pytest"})
        assert adapter.parse_hook_payload(json.dumps(no_wd)).cwd == "/work/cwd"
        no_wd.pop("cwd")
        assert adapter.parse_hook_payload(json.dumps(no_wd)).cwd == "/work/root"

    @pytest.mark.parametrize(
        "mutation",
        [
            {"hook_event_name": "beforeShellExecution"},
            {"hook_event_name": "postToolUse"},
            {"hook_event_name": "PreToolUse"},
            {"tool_name": "Read"},
            {"tool_name": "MCP:get_context"},
            {"tool_input": {"command": "   "}},
            {"tool_input": "pytest"},
        ],
    )
    def test_rejects_other_shapes(self, adapter, mutation) -> None:
        assert adapter.parse_hook_payload(json.dumps(_payload(**mutation))) is None

    @pytest.mark.parametrize("raw", ["", "{", "[]", "null", "7"])
    def test_malformed_input_never_raises(self, adapter, raw) -> None:
        assert adapter.parse_hook_payload(raw) is None


class TestRenderResponse:
    def test_echoes_the_whole_input_with_the_command_replaced(self, adapter) -> None:
        adapter.parse_hook_payload(json.dumps(_payload()))
        out = json.loads(
            adapter.render_response(RewriteResult("repowise distill pytest -x", "allow", "r"))
        )
        assert out == {
            "permission": "allow",
            "updated_input": {
                "command": "repowise distill pytest -x",
                "working_directory": "/work/repo",
            },
        }

    def test_capabilities(self, adapter) -> None:
        assert adapter.rewrite_permissions == frozenset({"allow"})
        assert adapter.replaces_tool_output is False
        assert adapter.shell_tool_names == frozenset({"Shell"})
        assert isinstance(adapter_for("cursor"), CursorAdapter)


def _run_main(monkeypatch, payload: dict) -> str:
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "argv", ["repowise-rewrite", "--agent", "cursor"])
    with pytest.raises(SystemExit):
        rewrite_hook.main()
    return stdout.getvalue()


class TestMain:
    def test_default_allow_rewrites(self, monkeypatch, tmp_path) -> None:
        (tmp_path / ".repowise").mkdir()
        payload = _payload(tool_input={"command": "pytest -x", "working_directory": str(tmp_path)})
        out = json.loads(_run_main(monkeypatch, payload))
        assert out["permission"] == "allow"
        assert out["updated_input"]["command"] == "repowise distill --source hook-cursor pytest -x"
        assert out["updated_input"]["working_directory"] == str(tmp_path)

    def test_ask_passes_through(self, monkeypatch, tmp_path) -> None:
        # preToolUse does not enforce "ask", so an ask family runs unrewritten.
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "config.yaml").write_text(
            "distill:\n  commands:\n    permission: ask\n", encoding="utf-8"
        )
        payload = _payload(tool_input={"command": "pytest -x", "working_directory": str(tmp_path)})
        assert _run_main(monkeypatch, payload) == ""


class TestHooksJson:
    def test_fresh_install(self, cursor_hooks_json) -> None:
        assert install_cursor_rewrite_hook() == cursor_hooks_json
        doc = json.loads(cursor_hooks_json.read_text(encoding="utf-8"))
        assert doc == {
            "hooks": {
                "preToolUse": [
                    {"command": "repowise-rewrite --agent cursor", "matcher": "Shell", "timeout": 5}
                ]
            },
            "version": 1,
        }
        assert cursor_rewrite_hook_matcher() == "Shell"
        assert CursorAdapter().rewrite_hook_status().fires

    def test_idempotent(self, cursor_hooks_json) -> None:
        install_cursor_rewrite_hook()
        before = cursor_hooks_json.read_bytes()
        install_cursor_rewrite_hook()
        assert cursor_hooks_json.read_bytes() == before

    def test_coexists_with_user_hooks(self, cursor_hooks_json) -> None:
        user = {
            "version": 1,
            "hooks": {
                "preToolUse": [{"command": "./hooks/guard.sh", "matcher": "Shell"}],
                "afterFileEdit": [{"command": "./hooks/format.sh"}],
            },
        }
        cursor_hooks_json.write_text(json.dumps(user), encoding="utf-8")
        install_cursor_rewrite_hook()
        doc = json.loads(cursor_hooks_json.read_text(encoding="utf-8"))
        assert doc["hooks"]["preToolUse"][0] == {"command": "./hooks/guard.sh", "matcher": "Shell"}
        assert len(doc["hooks"]["preToolUse"]) == 2

        assert uninstall_cursor_rewrite_hook() is True
        assert json.loads(cursor_hooks_json.read_text(encoding="utf-8")) == user

    def test_uninstall_of_our_only_entry_removes_the_file(self, cursor_hooks_json) -> None:
        install_cursor_rewrite_hook()
        assert uninstall_cursor_rewrite_hook() is True
        assert not cursor_hooks_json.exists()
        assert uninstall_cursor_rewrite_hook() is False

    def test_malformed_file_is_left_alone(self, cursor_hooks_json) -> None:
        cursor_hooks_json.write_text("{not json", encoding="utf-8")
        assert install_cursor_rewrite_hook() is None
        assert uninstall_cursor_rewrite_hook() is False
        assert cursor_hooks_json.read_text(encoding="utf-8") == "{not json"


def test_rewrite_install_covers_cursor_when_detected(cursor_hooks_json, monkeypatch) -> None:
    from repowise.cli.commands.hook_cmd import _install_cursor_hook

    _install_cursor_hook()
    assert not cursor_hooks_json.exists()  # no ~/.cursor, nothing written

    monkeypatch.setattr(CursorAdapter, "detect", lambda self: True)
    _install_cursor_hook()
    assert cursor_rewrite_hook_matcher() == "Shell"


@pytest.mark.parametrize(
    ("command", "ours"),
    [
        ("repowise-rewrite --agent cursor", True),
        ("repowise-rewrite", True),
        (r"C:\venv\Scripts\repowise-rewrite.exe --agent cursor", True),
        (r'"C:\Program Files\rw\repowise-rewrite.exe" --agent cursor', True),
        ("/usr/local/bin/repowise-rewrite --agent cursor", True),
        ("./hooks/my-repowise-rewrite-wrapper.sh", False),
        ("bash -c 'repowise-rewrite --agent cursor'", False),
        ("node hooks/guard.js repowise-rewrite", False),
    ],
)
def test_ownership_is_the_executable_basename(command: str, ours: bool) -> None:
    assert cursor_module._is_rewrite_hook({"command": command}) is ours


def test_uninstall_leaves_a_user_wrapper_that_mentions_us(cursor_hooks_json) -> None:
    wrapper = {"command": "./hooks/repowise-rewrite-wrapper.sh", "matcher": "Shell"}
    cursor_hooks_json.write_text(
        json.dumps({"version": 1, "hooks": {"preToolUse": [wrapper]}}), encoding="utf-8"
    )
    assert cursor_rewrite_hook_matcher() is None
    assert uninstall_cursor_rewrite_hook() is False
    install_cursor_rewrite_hook()
    assert uninstall_cursor_rewrite_hook() is True
    doc = json.loads(cursor_hooks_json.read_text(encoding="utf-8"))
    assert doc["hooks"]["preToolUse"] == [wrapper]
