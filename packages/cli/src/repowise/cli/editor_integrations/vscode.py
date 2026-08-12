"""VS Code setup integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.agent_targets.targets import vscode as vscode_target
from repowise.cli.editor_setup import EditorSetupOptions


class VSCodeSetup:
    """Project-local VS Code setup integration.

    Writes the workspace MCP server config (.vscode/mcp.json) and recommends the
    repowise extension (.vscode/extensions.json). Both are repo-shared files, so
    they use the bare ``repowise`` command like the committed ``.mcp.json``.
    """

    #: Read from the descriptor rather than restated, so the ids have one home.
    integration_id = vscode_target.ID
    project_file_id = vscode_target.PROJECT_FILE_ID

    def write_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        if self.project_file_id in options.disabled_project_files:
            return
        _write_vscode_files(console_obj, repo_path)

    def register_client(self, console_obj: Any, repo_path: Path) -> None:
        """VS Code reads the workspace .vscode/mcp.json; no user-level setup needed."""

        return None

    def refresh_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        if self.project_file_id in options.disabled_project_files:
            return
        _write_vscode_files(console_obj, repo_path)


def _write_vscode_files(console_obj: Any, repo_path: Path) -> None:
    """Write or merge the managed .vscode files, skipping any JSONC file safely."""

    from repowise.cli.mcp_config import (
        save_vscode_extensions_config,
        save_vscode_mcp_config,
    )

    try:
        mcp_path = save_vscode_mcp_config(repo_path)
        console_obj.print(f"  [green]✓[/green] VS Code MCP configured ({mcp_path})")
    except ValueError:
        console_obj.print(
            "  [yellow].vscode/mcp.json left unchanged (not valid JSON; it may contain "
            'comments). Add a "repowise" server under "servers" manually.[/yellow]'
        )

    try:
        ext_path = save_vscode_extensions_config(repo_path)
        console_obj.print(f"  [green]✓[/green] VS Code extension recommended ({ext_path})")
    except ValueError:
        console_obj.print(
            "  [yellow].vscode/extensions.json left unchanged (not valid JSON; it may "
            'contain comments). Add "repowise-dev.repowise" to "recommendations" '
            "manually.[/yellow]"
        )
