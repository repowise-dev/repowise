from __future__ import annotations

import inspect
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console

from repowise.cli import mcp_config
from repowise.cli.commands import init_cmd, update_cmd
from repowise.cli.editor_integrations import claude as claude_integration
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations.claude import ClaudeCodeSetup
from repowise.cli.editor_integrations.defaults import get_default_disabled_project_files
from repowise.cli.editor_setup import (
    EditorSetupOptions,
    refresh_editor_project_files,
    resolve_editor_setup_options,
    write_editor_project_files,
)


def _silent_console() -> Console:
    return Console(file=StringIO(), force_terminal=False)


def test_register_editor_clients_skipped_when_env_set(monkeypatch) -> None:
    """REPOWISE_SKIP_EDITOR_SETUP makes global client registration a no-op.

    Headless / CI / benchmark indexing (incl. transient git worktrees) must not
    mutate the developer's global editor config or repoint the single global
    'repowise' MCP entry at a path that will be deleted.
    """
    from repowise.cli.editor_setup import register_editor_clients

    registered: list[Path] = []

    class FakeIntegration:
        def configure_options(self, c: Any, o: Any) -> Any:
            return o

        def write_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

        def register_client(self, c: Any, p: Path) -> None:
            registered.append(p)

        def refresh_project_files(self, c: Any, p: Path, o: Any) -> None:
            pass

    integrations = (FakeIntegration(),)

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    register_editor_clients(_silent_console(), Path("repo"), integrations=integrations)
    assert registered == []  # skipped

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    register_editor_clients(_silent_console(), Path("repo"), integrations=integrations)
    assert registered == [Path("repo")]  # runs when unset


def test_resolve_editor_setup_options_delegates_to_integrations() -> None:
    calls: list[tuple[str, frozenset[str], bool]] = []

    class FakeIntegration:
        def configure_options(
            self,
            console_obj: object,
            options: EditorSetupOptions,
        ) -> EditorSetupOptions:
            calls.append(
                (
                    "configure",
                    options.disabled_project_files,
                    options.prompt_for_project_files,
                )
            )
            return options.with_disabled_project_file("fake_instructions")

    options = resolve_editor_setup_options(
        _silent_console(),
        disabled_project_files={"cli_disabled"},
        prompt_for_project_files=True,
        integrations=(FakeIntegration(),),  # type: ignore[arg-type]
    )

    assert calls == [("configure", frozenset({"cli_disabled"}), True)]
    assert options.disabled_project_files == frozenset({"cli_disabled", "fake_instructions"})
    assert options.prompt_for_project_files is True


def test_default_disabled_project_files_maps_legacy_no_claude_flag() -> None:
    assert get_default_disabled_project_files() == ()
    assert get_default_disabled_project_files(no_claude_md=True) == ("claude_md",)


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


def test_write_editor_project_files_uses_pre_resolved_options(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path, EditorSetupOptions]] = []
    options = EditorSetupOptions(
        disabled_project_files=frozenset({"resolved"}),
        prompt_for_project_files=True,
    )

    def fake_save_mcp_config(repo_path: Path) -> Path:
        calls.append(("mcp", repo_path, options))
        return repo_path / ".repowise" / "mcp.json"

    class FakeIntegration:
        def write_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            received_options: EditorSetupOptions,
        ) -> None:
            calls.append(("fake-project", repo_path, received_options))

    monkeypatch.setattr(mcp_config, "save_mcp_config", fake_save_mcp_config)

    write_editor_project_files(
        _silent_console(),
        tmp_path,
        options=options,
        disabled_project_files={"ignored"},
        integrations=(FakeIntegration(),),  # type: ignore[arg-type]
    )

    assert calls == [
        ("mcp", tmp_path, options),
        ("fake-project", tmp_path, options),
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


def test_refresh_editor_project_files_delegates_to_integrations(tmp_path: Path) -> None:
    calls: list[tuple[str, Path, frozenset[str]]] = []

    class FakeIntegration:
        def refresh_project_files(
            self,
            console_obj: object,
            repo_path: Path,
            options: EditorSetupOptions,
        ) -> None:
            calls.append(("refresh", repo_path, options.disabled_project_files))

    refresh_editor_project_files(
        _silent_console(),
        tmp_path,
        options=EditorSetupOptions(disabled_project_files=frozenset({"skip"})),
        integrations=(FakeIntegration(),),  # type: ignore[arg-type]
    )

    assert calls == [("refresh", tmp_path, frozenset({"skip"}))]


def test_claude_refresh_project_files_writes_when_enabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(),
    )

    assert calls == [tmp_path]


def test_claude_refresh_project_files_skips_when_config_disabled(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "editor_files:\n  claude_md: false\n",
        encoding="utf-8",
    )

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(),
    )

    assert calls == []


def test_claude_refresh_project_files_skips_when_options_disable_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []

    async def fake_write_claude_md(repo_path: Path) -> None:
        calls.append(repo_path)

    monkeypatch.setattr(
        claude_integration,
        "_write_claude_md_async",
        fake_write_claude_md,
    )

    ClaudeCodeSetup().refresh_project_files(
        _silent_console(),
        tmp_path,
        EditorSetupOptions(disabled_project_files=frozenset({"claude_md"})),
    )

    assert calls == []


def test_update_command_uses_editor_refresh_abstraction() -> None:
    source = inspect.getsource(update_cmd.update_command.callback)

    assert "refresh_editor_project_files" in source
    assert "ClaudeMdGenerator" not in source
    assert "EditorFileDataFetcher" not in source
    assert "claude_md" not in source


def test_init_command_uses_editor_option_abstraction() -> None:
    source = inspect.getsource(init_cmd.init_command.callback) + inspect.getsource(
        init_cmd._workspace_init
    )

    assert "resolve_editor_setup_options" in source
    assert "write_editor_project_files" in source
    assert "interactive_claude_md_prompt" not in source
    assert 'disabled_project_files={"claude_md"}' not in source


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
