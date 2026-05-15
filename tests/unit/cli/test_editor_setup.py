from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console

from repowise.cli import mcp_config
from repowise.cli.editor_integrations import claude as claude_integration
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations.claude import ClaudeCodeSetup
from repowise.cli.editor_setup import (
    EditorSetupOptions,
    write_editor_project_files,
)


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_write_editor_project_files_saves_common_mcp_before_integrations(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, object, object | None]] = []

    def fake_save_mcp_config(repo_path: Path) -> Path:
        calls.append(("mcp", repo_path, None))
        return repo_path / ".repowise" / "mcp.json"

    class FakeIntegration:
        def write_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            options: EditorSetupOptions,
        ) -> None:
            calls.append(("fake-project", repo_path, options.disabled_project_files))

        def register_client(self, console_obj: object, repo_path: Path) -> None:
            raise AssertionError("not used")

    monkeypatch.setattr(mcp_config, "save_mcp_config", fake_save_mcp_config)

    write_editor_project_files(
        _silent_console(),
        tmp_path,
        disabled_project_files={"fake_instructions"},
        integrations=(FakeIntegration(),),
    )

    assert calls == [
        ("mcp", tmp_path, None),
        ("fake-project", tmp_path, frozenset({"fake_instructions"})),
    ]


def test_claude_project_setup_writes_root_mcp_and_claude_md(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, object, object | None]] = []

    def fake_save_root_mcp_config(repo_path: Path) -> Path:
        calls.append(("root-mcp", repo_path, None))
        return repo_path / ".mcp.json"

    def fake_maybe_generate_claude_md(
        console_obj: object,
        repo_path: Path,
        *,
        no_claude_md: bool = False,
    ) -> None:
        calls.append(("claude-md", repo_path, no_claude_md))

    monkeypatch.setattr(mcp_config, "save_root_mcp_config", fake_save_root_mcp_config)
    monkeypatch.setattr(
        claude_integration,
        "maybe_generate_claude_md",
        fake_maybe_generate_claude_md,
    )

    ClaudeCodeSetup().write_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"claude_md"})),
    )

    assert calls == [
        ("root-mcp", tmp_path, None),
        ("claude-md", tmp_path, True),
    ]


def test_claude_client_registration_uses_existing_claude_setup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_desktop(repo_path: Path) -> Path:
        calls.append(("desktop", repo_path))
        return tmp_path / "claude_desktop_config.json"

    def fake_code(repo_path: Path) -> Path:
        calls.append(("code", repo_path))
        return tmp_path / ".claude" / "settings.json"

    def fake_hooks() -> Path:
        calls.append(("hooks", tmp_path))
        return tmp_path / ".claude" / "settings.json"

    monkeypatch.setattr(claude_config, "register_with_claude_desktop", fake_desktop)
    monkeypatch.setattr(claude_config, "register_with_claude_code", fake_code)
    monkeypatch.setattr(claude_config, "install_claude_code_hooks", fake_hooks)

    output = StringIO()
    console = Console(file=output, force_terminal=False)

    ClaudeCodeSetup().register_client(console, tmp_path)

    assert calls == [
        ("desktop", tmp_path),
        ("code", tmp_path),
        ("hooks", tmp_path),
    ]
    text = output.getvalue()
    assert "Claude Desktop MCP registered" in text
    assert "Claude Code MCP registered" in text
    assert "Claude Code hooks registered" in text
