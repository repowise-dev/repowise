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
    docs_flag: bool | None = None,
    index_only: bool = False,
    since: str | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    reasoning: str | None = None,
    cascade_budget: int | None = None,
    concurrency: int = 10,
    no_cost_tracking: bool = False,
    progress: str = "rich",
) -> None:
    """Update stale repos in a workspace.

    Takes a resolved :class:`CommandTarget` so the caller has full control
    over how the workspace was located (auto-detected vs explicit flag).

    Each stale repo resolves its own docs-vs-index-only mode the same way a
    single-repo update does, from its persisted ``docs_enabled`` plus any
    ``--docs`` / ``--no-docs`` / ``--index-only`` override passed here. Repos
    that want docs are updated through the full single-repo docs path (so
    their wiki pages, diagrams, and decisions stay as current as they would
    under ``repowise update`` run inside the repo); the rest take the fast
    parallel index-only path. See :func:`_workspace_docs_update`.
    """
    from repowise.cli.helpers import load_state
    from repowise.core.workspace import (
        check_repo_staleness,
        reconcile_repo_head_commit,
        update_workspace,
    )

    from .mode import _resolve_index_only_mode

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
    up_to_date_repos: list[tuple[Path, str]] = []
    # Aliases of stale repos whose effective mode is docs (indexed + docs
    # wanted). Everything else stale takes the fast index-only path, including
    # never-indexed repos, which can only be first-time indexed index-only, and
    # a follow-up ``--docs`` run generates their pages once they have an index.
    docs_aliases: set[str] = set()
    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        abs_path = (ws_root / entry.path).resolve()
        stored = entry.last_commit_at_index
        is_stale, head, behind = check_repo_staleness(abs_path, stored)
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
            if indexed and not _resolve_index_only_mode(
                index_only=index_only, docs_flag=docs_flag, state=load_state(abs_path)
            ):
                docs_aliases.add(entry.alias)
        if indexed and not is_stale:
            up_to_date_count += 1
            if head:
                up_to_date_repos.append((abs_path, head))
            if not verbose:
                continue
        console.print(f"  {entry.alias:<20} {status}")

    if up_to_date_count and not verbose:
        console.print(f"  [dim]{up_to_date_count} repo(s) up to date[/dim]")

    console.print()

    # Reconcile the DB freshness stamp for up-to-date repos even when nothing
    # needs regenerating. Staleness above is measured against the workspace
    # config's last_commit_at_index, but the server's /api/repos endpoint and
    # the MCP _meta check read repositories.head_commit from each repo's DB — a
    # row left behind by an older build keeps "index behind checkout" stuck
    # here, since the all-up-to-date path returns without calling
    # update_workspace. reconcile_repo_head_commit only writes on drift.
    if not dry_run and up_to_date_repos:

        async def _reconcile_up_to_date() -> None:
            for repo_path, head in up_to_date_repos:
                await reconcile_repo_head_commit(repo_path, head)

        run_async(_reconcile_up_to_date())

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

    # Repos that want docs go through the full single-repo docs path so their
    # wiki (pages, diagrams, decisions) stays as fresh as a single-repo update
    # would keep it; everything else stale takes the fast parallel index-only
    # path below. Branching here keeps the common all-index-only workspace
    # update byte-for-byte on its original path with no behavior change.
    if docs_aliases:
        _workspace_docs_update(
            ws_root=ws_root,
            ws_config=ws_config,
            repo_filter=repo_alias,
            docs_aliases=docs_aliases,
            start=start,
            agents_md=agents_md,
            verbose=verbose,
            docs_flag=docs_flag,
            index_only=index_only,
            since=since,
            provider_name=provider_name,
            model=model,
            reasoning=reasoning,
            cascade_budget=cascade_budget,
            concurrency=concurrency,
            no_cost_tracking=no_cost_tracking,
            progress=progress,
        )
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


def _workspace_docs_update(
    *,
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    docs_aliases: set[str],
    start: float,
    agents_md: bool | None,
    verbose: bool,
    docs_flag: bool | None,
    index_only: bool,
    since: str | None,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    cascade_budget: int | None,
    concurrency: int,
    no_cost_tracking: bool,
    progress: str,
) -> None:
    """Update a workspace where at least one stale repo wants docs.

    Docs repos (``docs_aliases``) run through the full single-repo update
    (:func:`run_update`) so their pages, diagrams, and decisions regenerate
    exactly as they would with ``repowise update`` run inside the repo. The
    remaining stale members (index-only + never-indexed first-timers) take the
    fast parallel core path. Cross-repo hooks are suppressed on both and run
    once at the end over every repo that actually changed, so the cross-repo
    layer is never rebuilt from a half-updated set.
    """
    from contextlib import suppress

    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict
    from repowise.core.workspace import RepoUpdateResult, update_workspace
    from repowise.core.workspace.update import (
        run_cross_repo_hooks,
        sync_workspace_state_from_disk,
    )

    from .command import run_update

    changed_aliases: list[str] = []
    docs_updated = 0
    docs_failed = 0

    # --- Index-only + first-time members: fast parallel core path ----------
    # only_aliases bounds the candidate set; update_workspace still re-checks
    # staleness, so any up-to-date member in the set simply no-ops.
    core_aliases = {
        entry.alias
        for entry in ws_config.repos
        if (repo_filter is None or entry.alias == repo_filter)
        and entry.alias not in docs_aliases
    }
    core_results: list[RepoUpdateResult] = []
    if core_aliases:

        def _on_start(alias: str) -> None:
            console.print(f"  Updating [bold]{alias}[/bold]...")

        def _on_done(result: RepoUpdateResult) -> None:
            if result.error:
                console.print(f"    [red]✗ {result.alias}: {result.error}[/red]")
            elif result.skipped_reason == "in_flight":
                console.print(
                    f"    [yellow]↻ {result.alias}: another update is already "
                    "in flight; this commit was queued for it to pick up.[/yellow]"
                )
            elif result.updated:
                console.print(
                    f"    [green]✓[/green] {result.alias}: "
                    f"{result.file_count} files, {result.symbol_count:,} symbols"
                )

        core_results = run_async(
            update_workspace(
                ws_root,
                ws_config,
                only_aliases=core_aliases,
                run_hooks=False,
                dry_run=False,
                on_repo_start=_on_start,
                on_repo_done=_on_done,
            )
        )
        changed_aliases.extend(r.alias for r in core_results if r.updated)

    # --- Docs members: full single-repo docs update, one at a time ---------
    for entry in ws_config.repos:
        if entry.alias not in docs_aliases:
            continue
        repo_path = (ws_root / entry.path).resolve()
        console.print(f"  Updating [bold]{entry.alias}[/bold] (docs)...")
        try:
            run_update(
                path=str(repo_path),
                provider_name=provider_name,
                model=model,
                since=since,
                reasoning=reasoning,
                cascade_budget=cascade_budget,
                dry_run=False,
                workspace=False,
                no_workspace=True,
                repo_alias=None,
                index_only=index_only,
                docs_flag=docs_flag,
                full=False,
                agents_md=agents_md,
                concurrency=concurrency,
                no_cost_tracking=no_cost_tracking,
                verbose=verbose,
                # rich even when the outer run is --progress json: the parent
                # already redirected the console to stderr and owns the single
                # stdout JSON event stream, so a nested json emitter would
                # corrupt it. The per-repo rich output goes to stderr there.
                progress="rich",
                skip_cross_repo_hooks=True,
            )
        except Exception as exc:
            docs_failed += 1
            console.print(f"    [red]✗ {entry.alias}: {exc}[/red]")
            continue
        docs_updated += 1
        changed_aliases.append(entry.alias)

    # The single-repo path only writes each repo's state.json, not the
    # workspace config, so re-sync it: the docs repos' advanced last_sync_commit
    # then lands in last_commit_at_index and the next run sees them up to date.
    with suppress(Exception):
        sync_workspace_state_from_disk(ws_root, ws_config)

    # Backfill the distill rewrite-hook verdict for any first-time member the
    # core path just indexed (matches the index-only workspace path).
    for entry in ws_config.repos:
        if repo_filter and entry.alias != repo_filter:
            continue
        with suppress(Exception):
            inherit_workspace_distill_verdict((ws_root / entry.path).resolve())

    # Cross-repo hooks once, over the union of index-only and docs repos.
    if changed_aliases and len(ws_config.repos) >= 2:
        console.print("Running cross-repo analysis...")
        with suppress(Exception):
            run_async(run_cross_repo_hooks(ws_config, ws_root, changed_aliases))
        console.print("[green]Cross-repo analysis updated.[/green]")

    # Editor files: re-stamp every member. Docs repos were already stamped by
    # their single-repo run; the core repos need it here. Idempotent.
    _refresh_workspace_editor_project_files(
        ws_root=ws_root,
        ws_config=ws_config,
        repo_filter=repo_filter,
        agents_md=agents_md,
    )

    core_updated = sum(1 for r in core_results if r.updated)
    parts: list[str] = []
    if docs_updated:
        parts.append(f"{docs_updated} with docs regenerated")
    if core_updated:
        parts.append(f"{core_updated} re-indexed")
    if docs_failed:
        parts.append(f"[red]{docs_failed} failed[/red]")
    summary = ", ".join(parts) if parts else "nothing to update"
    console.print()
    console.print(
        f"[green]Workspace update complete[/green]: {summary} "
        f"[dim]({time.monotonic() - start:.1f}s)[/dim]"
    )


def _refresh_workspace_editor_project_files(
    *,
    ws_root: Path,
    ws_config: Any,
    repo_filter: str | None,
    agents_md: bool | None,
) -> None:
    """Re-stamp each workspace repo's editor files (CLAUDE.md / AGENTS.md).

    Runs on every workspace update so a repo's ``.claude/CLAUDE.md`` "Last
    indexed" stamp tracks the freshly synced index. Each integration decides
    whether to write from the repo's own config (CLAUDE.md defaults on), so we
    pass ``options=None`` for the common case.

    ``agents_md`` is only a per-run override for AGENTS.md generation; when it
    is ``None`` each integration falls back to config. It must NOT gate the
    whole refresh: doing so froze the CLAUDE.md stamp for every workspace user
    who never passed ``--agents-md`` (the default), making the index look stale
    long after it was current. Mirrors the single-repo update path.
    """
    from repowise.cli.editor_integrations.defaults import get_default_project_file_overrides
    from repowise.cli.editor_setup import EditorSetupOptions, refresh_editor_project_files

    options: EditorSetupOptions | None = None
    if agents_md is not None:
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
