"""``repowise watch`` — watch for file changes and auto-update."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import click

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    find_workspace_root,
    resolve_repo_path,
    run_async,
)


# ---------------------------------------------------------------------------
# Single-repo watch (existing behavior)
# ---------------------------------------------------------------------------


def _watch_single_repo(
    repo_path: Path,
    provider_name: str | None,
    model: str | None,
    debounce_ms: int,
) -> None:
    """Watch a single repo for changes and trigger updates."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    ensure_repowise_dir(repo_path)

    changed_paths: set[str] = set()
    lock = threading.Lock()
    timer: threading.Timer | None = None

    def _on_trigger() -> None:
        nonlocal timer
        with lock:
            paths = set(changed_paths)
            changed_paths.clear()
            timer = None

        if not paths:
            return

        console.print(f"[cyan]Detected {len(paths)} changed file(s), updating...[/cyan]")
        try:
            from click.testing import CliRunner

            from repowise.cli.commands.update_cmd import update_command

            runner = CliRunner()
            args = [str(repo_path)]
            if provider_name:
                args.extend(["--provider", provider_name])
            if model:
                args.extend(["--model", model])
            result = runner.invoke(update_command, args, catch_exceptions=False)
            if result.output:
                console.print(result.output)
        except Exception as e:
            console.print(f"[red]Update failed: {e}[/red]")

    class RepowiseHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            nonlocal timer
            if event.is_directory:
                return
            rel = str(Path(event.src_path).relative_to(repo_path))
            if rel.startswith(".repowise"):
                return

            with lock:
                changed_paths.add(rel)
                if timer is not None:
                    timer.cancel()
                timer = threading.Timer(debounce_ms / 1000.0, _on_trigger)
                timer.daemon = True
                timer.start()

    observer = Observer()
    observer.schedule(RepowiseHandler(), str(repo_path), recursive=True)
    observer.start()

    console.print(f"[bold]Watching {repo_path}... Ctrl+C to stop[/bold]")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[yellow]Stopped watching.[/yellow]")
    observer.join()


# ---------------------------------------------------------------------------
# Workspace watch — per-repo handlers with independent debounce
# ---------------------------------------------------------------------------


def _watch_workspace(
    ws_root: Path,
    debounce_ms: int,
) -> None:
    """Watch all repos in a workspace with per-repo debounce timers."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    from repowise.core.workspace import WorkspaceConfig, update_workspace

    ws_config = WorkspaceConfig.load(ws_root)

    if not ws_config.repos:
        console.print("[yellow]No repos in workspace config.[/yellow]")
        return

    # Per-repo state: each repo gets its own change set and debounce timer
    repo_locks: dict[str, threading.Lock] = {}
    repo_changed: dict[str, set[str]] = {}
    repo_timers: dict[str, threading.Timer | None] = {}

    for entry in ws_config.repos:
        repo_locks[entry.alias] = threading.Lock()
        repo_changed[entry.alias] = set()
        repo_timers[entry.alias] = None

    def _make_trigger(alias: str) -> callable:
        """Create a trigger function for a specific repo."""

        def _on_trigger() -> None:
            with repo_locks[alias]:
                paths = set(repo_changed[alias])
                repo_changed[alias].clear()
                repo_timers[alias] = None

            if not paths:
                return

            console.print(
                f"[cyan]{alias}: {len(paths)} changed file(s), updating...[/cyan]"
            )
            try:
                # Reload config in case it was updated
                current_config = WorkspaceConfig.load(ws_root)
                results = run_async(
                    update_workspace(
                        ws_root,
                        current_config,
                        repo_filter=alias,
                    )
                )
                for r in results:
                    if r.error:
                        console.print(f"  [red]{alias}: {r.error}[/red]")
                    elif r.updated:
                        console.print(
                            f"  [green]\u2713[/green] {alias}: "
                            f"{r.file_count} files, {r.symbol_count:,} symbols"
                        )
                    elif r.skipped_reason == "up_to_date":
                        console.print(f"  [dim]{alias}: already up to date[/dim]")
            except Exception as e:
                console.print(f"  [red]{alias} update failed: {e}[/red]")

        return _on_trigger

    class WorkspaceRepoHandler(FileSystemEventHandler):
        """Handler for a single repo directory within the workspace."""

        def __init__(self, alias: str, repo_path: Path):
            super().__init__()
            self._alias = alias
            self._repo_path = repo_path

        def on_any_event(self, event):
            if event.is_directory:
                return
            try:
                rel = str(Path(event.src_path).relative_to(self._repo_path))
            except ValueError:
                return
            if rel.startswith(".repowise"):
                return

            alias = self._alias
            with repo_locks[alias]:
                repo_changed[alias].add(rel)
                old_timer = repo_timers[alias]
                if old_timer is not None:
                    old_timer.cancel()
                new_timer = threading.Timer(
                    debounce_ms / 1000.0, _make_trigger(alias),
                )
                new_timer.daemon = True
                new_timer.start()
                repo_timers[alias] = new_timer

    observer = Observer()
    scheduled = 0
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        if not abs_path.is_dir():
            console.print(f"  [yellow]Skipping {entry.alias}: directory not found[/yellow]")
            continue
        handler = WorkspaceRepoHandler(entry.alias, abs_path)
        observer.schedule(handler, str(abs_path), recursive=True)
        scheduled += 1

    if scheduled == 0:
        console.print("[yellow]No repo directories found to watch.[/yellow]")
        return

    observer.start()

    repo_list = ", ".join(e.alias for e in ws_config.repos)
    console.print(f"[bold]Watching workspace ({scheduled} repos: {repo_list})... Ctrl+C to stop[/bold]")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[yellow]Stopped watching.[/yellow]")
    observer.join()


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("watch")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--debounce", "debounce_ms", type=int, default=2000, help="Debounce delay in ms.")
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Watch all repos in the workspace.",
)
def watch_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    debounce_ms: int,
    workspace: bool,
) -> None:
    """Watch for file changes and auto-update wiki pages."""
    repo_path = resolve_repo_path(path)

    if workspace:
        ws_root = find_workspace_root(repo_path)
        if ws_root is None:
            raise click.ClickException(
                "No .repowise-workspace.yaml found. "
                "Run 'repowise init <workspace-dir>' first."
            )
        _watch_workspace(ws_root, debounce_ms)
    else:
        _watch_single_repo(repo_path, provider_name, model, debounce_ms)
