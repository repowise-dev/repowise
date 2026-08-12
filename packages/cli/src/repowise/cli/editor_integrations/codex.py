"""Codex setup integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.agent_targets.targets import codex as codex_target
from repowise.cli.editor_setup import EditorSetupOptions


class CodexSetup:
    """Project-local Codex setup integration.

    The ``init``/``update`` half of the integration; the descriptor in
    ``agent_targets.targets.codex`` owns the writes and everything else.
    """

    #: Read from the descriptor rather than restated, so the ids have one home.
    integration_id = codex_target.ID
    project_file_id = codex_target.PROJECT_FILE_ID

    def write_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        from repowise.cli.mcp_config import (
            is_codex_cli_installed,
            is_codex_logged_in,
            save_codex_hooks_config,
            save_codex_mcp_config,
        )

        setup_override = options.integration_overrides.get(self.integration_id)
        agents_override = options.project_file_overrides.get(self.project_file_id)
        agents_override_present = self.project_file_id in options.project_file_overrides
        if setup_override is False:
            if agents_override_present:
                maybe_generate_agents_md(console_obj, repo_path, agents_md=agents_override)
            return
        if setup_override is None:
            if agents_override_present:
                maybe_generate_agents_md(console_obj, repo_path, agents_md=agents_override)
            return

        installed = is_codex_cli_installed()
        logged_in = is_codex_logged_in() if installed else False

        config_path = save_codex_mcp_config(repo_path)
        console_obj.print(f"  [green]✓[/green] Codex MCP registered ({config_path})")
        hooks_path = save_codex_hooks_config(repo_path)
        console_obj.print(f"  [green]✓[/green] Codex hooks registered ({hooks_path})")
        maybe_generate_agents_md(
            console_obj,
            repo_path,
            agents_md=True if agents_override is None else agents_override,
        )

        if setup_override is True and not installed:
            console_obj.print(
                "  [yellow]Codex CLI was not detected; install with "
                "'npm install -g @openai/codex' before using this config.[/yellow]"
            )
        elif setup_override is True and not logged_in:
            console_obj.print(
                "  [yellow]Codex CLI is not logged in; run 'codex login' before using this config.[/yellow]"
            )

    def register_client(self, console_obj: Any, repo_path: Path) -> None:
        """Codex setup is project-local and does not require global registration."""

        return None

    def refresh_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        if self.project_file_id in options.disabled_project_files:
            return
        maybe_generate_agents_md(
            console_obj,
            repo_path,
            agents_md=options.project_file_overrides.get(self.project_file_id),
            default=False,
        )


def maybe_generate_agents_md(
    console_obj: Any,
    repo_path: Path,
    *,
    agents_md: bool | None = None,
    default: bool = True,
) -> None:
    """Generate AGENTS.md if enabled in config and not opted out."""

    from repowise.cli.editor_files import should_generate_editor_file
    from repowise.cli.helpers import run_async
    from repowise.cli.ui import OWL_SPINNER

    if not should_generate_editor_file(
        repo_path,
        "agents_md",
        default=default,
        override=agents_md,
    ):
        return
    try:
        with console_obj.status("  Generating AGENTS.md…", spinner=OWL_SPINNER):
            run_async(_write_agents_md_async(repo_path))
        console_obj.print("  [green]✓[/green] AGENTS.md updated")
    except Exception as exc:
        console_obj.print(f"  [yellow]AGENTS.md skipped: {exc}[/yellow]")


async def _write_agents_md_async(repo_path: Path) -> None:
    """Fetch indexed repo data and write AGENTS.md."""

    from repowise.cli.editor_files import write_agents_md

    await write_agents_md(repo_path)
