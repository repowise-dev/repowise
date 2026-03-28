"""``repowise watch`` — watch for file changes and auto-update."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import click

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    resolve_repo_path,
)


@click.command("watch")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--debounce", "debounce_ms", type=int, default=2000, help="Debounce delay in ms.")
def watch_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    debounce_ms: int,
) -> None:
    """Watch for file changes and auto-update wiki pages."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    repo_path = resolve_repo_path(path)
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
            # Invoke update via Click context
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
            # Ignore .repowise/ directory changes
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
