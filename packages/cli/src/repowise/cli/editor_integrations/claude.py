"""Claude Code/Desktop setup integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.editor_setup import EditorSetupOptions
from repowise.cli.helpers import get_db_url_for_repo, load_config, run_async


class ClaudeCodeSetup:
    """Claude Code/Desktop setup integration preserving existing init behavior."""

    def write_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        from repowise.cli.mcp_config import save_root_mcp_config

        save_root_mcp_config(repo_path)
        maybe_generate_claude_md(
            console_obj,
            repo_path,
            no_claude_md="claude_md" in options.disabled_project_files,
        )

    def register_client(self, console_obj: Any, repo_path: Path) -> None:
        from repowise.cli.editor_integrations.claude_config import (
            install_claude_code_hooks,
            register_with_claude_code,
            register_with_claude_desktop,
        )

        desktop = register_with_claude_desktop(repo_path)
        if desktop:
            console_obj.print(f"  [green]✓[/green] Claude Desktop MCP registered ({desktop})")

        code = register_with_claude_code(repo_path)
        if code:
            console_obj.print(f"  [green]✓[/green] Claude Code MCP registered ({code})")

        hooks = install_claude_code_hooks()
        if hooks:
            console_obj.print(
                "  [green]✓[/green] Claude Code hooks registered (PreToolUse + PostToolUse)"
            )


def maybe_generate_claude_md(
    console_obj: Any,
    repo_path: Path,
    *,
    no_claude_md: bool = False,
) -> None:
    """Generate CLAUDE.md if enabled in config and not opted out."""

    cfg = load_config(repo_path)
    enabled = cfg.get("editor_files", {}).get("claude_md", True)
    if no_claude_md:
        # Persist opt-out so 'repowise update' respects it.
        ef_cfg = dict(cfg.get("editor_files", {}))
        ef_cfg["claude_md"] = False
        cfg["editor_files"] = ef_cfg
        try:
            import yaml  # type: ignore[import-untyped]

            cfg_path = repo_path / ".repowise" / "config.yaml"
            cfg_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except ImportError:
            pass
        return
    if not enabled:
        return
    try:
        with console_obj.status("  Generating .claude/CLAUDE.md…", spinner="dots"):
            run_async(_write_claude_md_async(repo_path))
        console_obj.print("  [green]✓[/green] .claude/CLAUDE.md updated")
    except Exception as exc:
        console_obj.print(f"  [yellow].claude/CLAUDE.md skipped: {exc}[/yellow]")


async def _write_claude_md_async(repo_path: Path) -> None:
    """Fetch indexed repo data and write CLAUDE.md."""

    from repowise.core.generation.editor_files import ClaudeMdGenerator, EditorFileDataFetcher
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.crud import get_repository_by_path

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return
            fetcher = EditorFileDataFetcher(session, repo.id, repo_path)
            data = await fetcher.fetch()
    finally:
        await engine.dispose()
    ClaudeMdGenerator().write(repo_path, data)
