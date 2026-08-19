"""Claude Code/Desktop setup integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.agent_targets.targets import claude_code as claude_target
from repowise.cli.editor_setup import EditorSetupOptions
from repowise.cli.helpers import get_db_url_for_repo, load_config, run_async


class ClaudeCodeSetup:
    """Claude Code/Desktop setup integration preserving existing init behavior.

    The ``init``/``update`` half of the integration. Detection, uninstall,
    ``print_config`` and ``doctor`` live on the descriptor in
    ``agent_targets.targets.claude_code``, along with every config write this
    class drives.
    """

    #: Read from the descriptor rather than restated, so the ids have one home.
    integration_id = claude_target.ID
    project_file_id = claude_target.PROJECT_FILE_ID

    def write_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> list[Path]:
        from repowise.cli.mcp_config import save_root_mcp_config

        written = [Path(save_root_mcp_config(repo_path))]
        claude_md = maybe_generate_claude_md(
            console_obj,
            repo_path,
            no_claude_md=self.project_file_id in options.disabled_project_files,
        )
        if claude_md is not None:
            written.append(claude_md)
        return written

    def register_client(self, console_obj: Any, repo_path: Path) -> None:
        from repowise.cli.editor_integrations.claude_config import (
            describe_mcp_registration_change,
            enable_tool_search_in_claude_code,
            install_claude_code_hooks,
            register_with_claude_code,
            register_with_claude_desktop,
        )
        from repowise.cli.ui.brand import OK, WARN

        # Read-only probe first: the merge below silently repoints the single
        # global "repowise" entry, so say so before it happens.
        clobber = describe_mcp_registration_change(repo_path)
        if clobber:
            console_obj.print(f"  [{WARN}]![/] {clobber}")

        desktop = register_with_claude_desktop(repo_path)
        if desktop:
            console_obj.print(f"  [{OK}]✓[/] Claude Desktop MCP registered ({desktop})")

        code = register_with_claude_code(repo_path)
        if code:
            console_obj.print(f"  [{OK}]✓[/] Claude Code MCP registered ({code})")

        hooks = install_claude_code_hooks()
        if hooks:
            console_obj.print(
                f"  [{OK}]✓[/] Claude Code hooks registered (PostToolUse, SessionStart)"
            )

        if _uses_lean_tool_surface(repo_path):
            console_obj.print(
                "  [dim]Lean MCP tool surface configured; skipping tool-search"
                " deferral so the core schemas stay always loaded.[/dim]"
            )
        elif enable_tool_search_in_claude_code():
            console_obj.print(
                f"  [{OK}]✓[/] Claude Code tool-search enabled (defers MCP tool schemas)"
            )

    def refresh_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        if self.project_file_id in options.disabled_project_files:
            return
        if not _claude_md_enabled(repo_path):
            return
        run_async(_write_claude_md_async(repo_path))


def _uses_lean_tool_surface(repo_path: Path) -> bool:
    """True when the repo's ``mcp.tools`` config selects the lean profile.

    The lean surface (~1.8k tokens of schema) is cheap enough to keep always
    loaded, so init skips the tool-search (schema deferral) recommendation for
    it; deferral would reintroduce the schema-load round trip the profile
    exists to remove. Mirrors the "lean" token the selection layer resolves
    (see ``repowise.server.mcp_server._tool_selection.LEAN``), read here via
    the lightweight config loader so init does not import the server stack.
    """
    try:
        from repowise.core.repo_config import load_repo_config

        mcp_cfg = load_repo_config(str(repo_path)).get("mcp") or {}
        tools = mcp_cfg.get("tools") if isinstance(mcp_cfg, dict) else None
    except Exception:
        return False
    if isinstance(tools, str):
        return tools.strip().lower() == "lean"
    if isinstance(tools, (list, tuple)) and len(tools) == 1:
        return str(tools[0]).strip().lower() == "lean"
    return False


def _claude_md_enabled(repo_path: Path) -> bool:
    cfg = load_config(repo_path)
    return bool(cfg.get("editor_files", {}).get("claude_md", True))


def maybe_generate_claude_md(
    console_obj: Any,
    repo_path: Path,
    *,
    no_claude_md: bool = False,
) -> Path | None:
    """Generate CLAUDE.md if enabled in config and not opted out.

    Returns the path written, or ``None`` for every path that does not write:
    opted out, disabled in config, or the generator raising. The completion
    panel's manifest is built from these, so "we tried and it failed" has to
    read as "not written" rather than being reported to the user as a file that
    is now in their tree.
    """

    cfg = load_config(repo_path)
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
        return None
    if not _claude_md_enabled(repo_path):
        return None

    from repowise.cli.ui import OWL_SPINNER
    from repowise.cli.ui.brand import OK, WARN

    try:
        with console_obj.status("  Generating .claude/CLAUDE.md…", spinner=OWL_SPINNER):
            written = run_async(_write_claude_md_async(repo_path))
    except Exception as exc:
        console_obj.print(f"  [{WARN}].claude/CLAUDE.md skipped: {exc}[/]")
        return None
    if written is None:
        # The repo is not in the database yet, so the generator had nothing to
        # write from and returned without touching disk. Silent before, which
        # is how a run could print no error and produce no file.
        console_obj.print(f"  [{WARN}].claude/CLAUDE.md skipped: repo not indexed yet[/]")
        return None
    console_obj.print(f"  [{OK}]✓[/] .claude/CLAUDE.md updated")
    return written


async def _write_claude_md_async(repo_path: Path) -> Path | None:
    """Fetch indexed repo data and write CLAUDE.md, returning the path.

    ``None`` when the repository is not in the database, which is the one path
    here that returns without writing. The caller reports it, because a
    manifest that lists a file nobody wrote is worse than one that lists none.
    """

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
                return None
            fetcher = EditorFileDataFetcher(session, repo.id, repo_path)
            data = await fetcher.fetch()
    finally:
        await engine.dispose()
    return ClaudeMdGenerator().write(repo_path, data)
