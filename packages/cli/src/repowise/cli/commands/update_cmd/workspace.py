"""``repowise update`` workspace flow \u2014 update stale repos in a workspace."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import CommandTarget, console, run_async


def _workspace_update(
    target: CommandTarget,
    *,
    dry_run: bool = False,
    agents_md: bool | None = None,
    verbose: bool = False,
) -> None:
    """Update stale repos in a workspace.

    Takes a resolved :class:`CommandTarget` so the caller has full control
    over how the workspace was located (auto-detected vs explicit flag).
    """
    from repowise.core.workspace import check_repo_staleness, update_workspace

    start = time.monotonic()
    ws_root = target.ws_root
    ws_config = target.ws_config
    repo_alias = target.repo_filter
    if ws_root is None or ws_config is None:
        # Defensive: callers should always pass a workspace-mode target,
        # but guard against misuse so the error message is clear.
        raise click.ClickException("_workspace_update called without a workspace target.")

    # Show staleness summary first
    console.print(f"[bold]repowise update[/bold] \u2014 workspace: {ws_root.name}")
    console.print()

    stale_count = 0
    up_to_date_count = 0
    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        abs_path = (ws_root / entry.path).resolve()
        stored = entry.last_commit_at_index
        is_stale, _head, behind = check_repo_staleness(abs_path, stored)
        indexed = (abs_path / ".repowise").is_dir()
        if not indexed:
            status = "[dim]not indexed[/dim]"
        elif is_stale:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
        else:
            status = "[green]up to date[/green]"
        # Default to a focused list (stale + not-indexed); up-to-date repos
        # collapse into a single count line unless -v lists everything.
        if is_stale:
            stale_count += 1
        if indexed and not is_stale:
            up_to_date_count += 1
            if not verbose:
                continue
        console.print(f"  {entry.alias:<20} {status}")

    if up_to_date_count and not verbose:
        console.print(f"  [dim]{up_to_date_count} repo(s) up to date[/dim]")

    console.print()

    if stale_count == 0:
        console.print("[green]All repos are up to date.[/green]")
        if not dry_run:
            _refresh_workspace_editor_project_files(
                ws_root=ws_root,
                ws_config=ws_config,
                repo_filter=repo_alias,
                agents_md=agents_md,
            )
        return

    if dry_run:
        console.print(f"[yellow]Dry run \u2014 {stale_count} repo(s) would be updated.[/yellow]")
        return

    # Run the updates
    def _on_start(alias: str) -> None:
        console.print(f"  Updating [bold]{alias}[/bold]...")

    def _on_done(result: RepoUpdateResult) -> None:
        if result.error:
            console.print(f"    [red]\u2717 {result.alias}: {result.error}[/red]")
        elif result.skipped_reason == "in_flight":
            # Surface skipped-because-in-flight as a yellow note rather than
            # a silent skip. Single-flight is the noise-fix path, so the
            # user benefits from seeing it actually trigger.
            console.print(
                f"    [yellow]\u21bb {result.alias}: another update is already "
                "in flight; this commit was queued for it to pick up.[/yellow]"
            )
        elif result.updated:
            console.print(
                f"    [green]\u2713[/green] {result.alias}: "
                f"{result.file_count} files, {result.symbol_count:,} symbols"
            )

    from repowise.core.workspace import RepoUpdateResult

    results = run_async(
        update_workspace(
            ws_root,
            ws_config,
            repo_filter=repo_alias,
            dry_run=False,
            on_repo_start=_on_start,
            on_repo_done=_on_done,
        )
    )

    # Backfill the distill rewrite-hook verdict for members that were just
    # indexed for the first time (e.g. added with --no-index) \u2014 they would
    # otherwise default to enabled despite a workspace-wide decline at init.
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        inherit_workspace_distill_verdict((ws_root / entry.path).resolve())

    # Summary
    updated = sum(1 for r in results if r.updated)
    errors = sum(1 for r in results if r.error)
    skipped = sum(1 for r in results if r.skipped_reason)
    total_files = sum(r.file_count for r in results if r.updated)
    total_symbols = sum(r.symbol_count for r in results if r.updated)

    _refresh_workspace_editor_project_files(
        ws_root=ws_root,
        ws_config=ws_config,
        repo_filter=repo_alias,
        agents_md=agents_md,
    )

    from .reporting import show_workspace_completion

    show_workspace_completion(
        ws_name=ws_root.name,
        updated=updated,
        skipped=skipped,
        errors=errors,
        total_files=total_files,
        total_symbols=total_symbols,
        elapsed=time.monotonic() - start,
    )


def _refresh_workspace_editor_project_files(
    *,
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    agents_md: bool | None,
) -> None:
    """Refresh workspace repo editor files for explicit per-run overrides."""

    if agents_md is None:
        return

    from repowise.cli.editor_integrations.defaults import get_default_project_file_overrides
    from repowise.cli.editor_setup import EditorSetupOptions, refresh_editor_project_files

    options = EditorSetupOptions(
        project_file_overrides=get_default_project_file_overrides(agents_md=agents_md),
    )
    for entry in ws_config.repos:
        if repo_filter and entry.alias != repo_filter:
            continue
        repo_path = (ws_root / entry.path).resolve()
        if not (repo_path / ".repowise").is_dir():
            continue
        try:
            refresh_editor_project_files(console, repo_path, options=options)
        except Exception as exc:
            console.print(
                f"  [yellow]{entry.alias}: editor project-file refresh skipped: {exc}[/yellow]"
            )
