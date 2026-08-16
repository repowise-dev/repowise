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
    ) -> list[Path]:
        from repowise.cli.mcp_config import (
            is_codex_cli_installed,
            is_codex_logged_in,
            save_codex_hooks_config,
            save_codex_mcp_config,
        )
        from repowise.cli.ui.brand import OK, WARN

        setup_override = options.integration_overrides.get(self.integration_id)
        agents_override = options.project_file_overrides.get(self.project_file_id)
        agents_override_present = self.project_file_id in options.project_file_overrides
        # Codex is opt-in, and stays that way. It is the one integration that
        # already got this right — nothing is written unless --codex or the
        # checklist asked for it — which is why #1499 names the other two.
        if setup_override is None or setup_override is False:
            if agents_override_present:
                written = maybe_generate_agents_md(
                    console_obj, repo_path, agents_md=agents_override
                )
                return [written] if written is not None else []
            return []

        installed = is_codex_cli_installed()
        logged_in = is_codex_logged_in() if installed else False

        config_path = save_codex_mcp_config(repo_path)
        console_obj.print(f"  [{OK}]✓[/] Codex MCP registered ({config_path})")
        hooks_path = save_codex_hooks_config(repo_path)
        console_obj.print(f"  [{OK}]✓[/] Codex hooks registered ({hooks_path})")
        written = [Path(config_path), Path(hooks_path)]
        agents_path = maybe_generate_agents_md(
            console_obj,
            repo_path,
            agents_md=True if agents_override is None else agents_override,
        )
        if agents_path is not None:
            written.append(agents_path)

        if not installed:
            console_obj.print(
                f"  [{WARN}]Codex CLI was not detected; install with "
                "'npm install -g @openai/codex' before using this config.[/]"
            )
        elif not logged_in:
            console_obj.print(
                f"  [{WARN}]Codex CLI is not logged in; run 'codex login' "
                "before using this config.[/]"
            )
        return written

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
) -> Path | None:
    """Generate AGENTS.md if enabled in config and not opted out.

    Returns the path written, or ``None`` for every path that does not write —
    same contract as ``maybe_generate_claude_md``, so the completion panel's
    manifest never claims a file the run failed to produce.
    """

    from repowise.cli.editor_files import should_generate_editor_file
    from repowise.cli.helpers import run_async
    from repowise.cli.ui import OWL_SPINNER
    from repowise.cli.ui.brand import OK, WARN

    if not should_generate_editor_file(
        repo_path,
        "agents_md",
        default=default,
        override=agents_md,
    ):
        return None
    try:
        with console_obj.status("  Generating AGENTS.md…", spinner=OWL_SPINNER):
            run_async(_write_agents_md_async(repo_path))
        console_obj.print(f"  [{OK}]✓[/] AGENTS.md updated")
    except Exception as exc:
        console_obj.print(f"  [{WARN}]AGENTS.md skipped: {exc}[/]")
        return None
    return repo_path / "AGENTS.md"


async def _write_agents_md_async(repo_path: Path) -> None:
    """Fetch indexed repo data and write AGENTS.md."""

    from repowise.cli.editor_files import write_agents_md

    await write_agents_md(repo_path)
