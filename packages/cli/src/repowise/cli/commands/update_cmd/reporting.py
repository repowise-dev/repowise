"""Console rendering for ``repowise update``.

Pure presentation: headers, the changed-file summary, the live generation
progress bar, and the completion panels for the three update paths (full,
index-only, workspace). Reuses the shared ``cli/ui`` panel + progress helpers
so ``update`` looks and feels like ``init``. No persistence or generation work
happens here.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.helpers import console
from repowise.cli.ui import (
    BRAND_STYLE,
    OWL_SPINNER,
    MaybeCountColumn,
    build_completion_panel,
    format_elapsed,
)

# Status -> display color, shared by the changed-file summary.
_STATUS_COLOR = {"added": "green", "deleted": "red", "modified": "yellow", "renamed": "blue"}

# How many changed files to list before collapsing to a "+N more" line.
_CHANGED_FILE_PREVIEW = 10


# ---------------------------------------------------------------------------
# Headers + changed-file summary
# ---------------------------------------------------------------------------


def render_header(repo_path: Any, base_ref: str, head: str | None) -> None:
    """Compact single-repo update header: repo name + the diff range."""
    console.print(f"[bold]repowise update[/bold] [dim]·[/dim] {repo_path.name}")
    console.print(f"[dim]{base_ref[:8]}..{(head or 'HEAD')[:8]}[/dim]")


def render_changed_files(file_diffs: list, *, verbose: bool) -> None:
    """Summarise changed files: a count breakdown, a short preview, then a
    ``+N more`` collapse — unless ``verbose`` is set, which lists them all.
    """
    from collections import Counter

    counts = Counter(fd.status for fd in file_diffs)
    breakdown = ", ".join(
        f"{counts[status]} {status}"
        for status in ("modified", "added", "deleted", "renamed")
        if counts.get(status)
    )
    summary = f"[bold]{len(file_diffs)}[/bold] changed"
    if breakdown:
        summary += f" [dim]·[/dim] {breakdown}"
    console.print(summary)

    shown = file_diffs if verbose else file_diffs[:_CHANGED_FILE_PREVIEW]
    for fd in shown:
        color = _STATUS_COLOR.get(fd.status, "white")
        console.print(f"  [{color}]{fd.status:>10}[/{color}]  {fd.path}")

    hidden = len(file_diffs) - len(shown)
    if hidden > 0:
        console.print(f"  [dim]+{hidden} more (use -v to list all)[/dim]")


# ---------------------------------------------------------------------------
# Live generation progress
# ---------------------------------------------------------------------------


def make_generation_progress() -> Progress:
    """Build the live page-generation progress bar (owl spinner + running cost),
    matching the columns ``init`` uses for its generation phase.
    """
    return Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    )


# ---------------------------------------------------------------------------
# Machine-readable progress (--progress json)
# ---------------------------------------------------------------------------


class JsonProgressEmitter:
    """Emits newline-delimited JSON progress events to stdout.

    One JSON object per line, flushed immediately, so a supervising process
    can stream ``repowise update`` progress without parsing Rich's terminal
    output. Pairs with ``silence_logs_for_machine_output`` and redirecting
    the Rich ``console`` to stderr, so stdout carries nothing but these
    events.
    """

    def _emit(self, event: dict[str, Any]) -> None:
        click.echo(json.dumps(event))
        sys.stdout.flush()

    def start(self, *, repo: str, since: str | None) -> None:
        self._emit({"event": "start", "repo": repo, "since": since})

    def stage(self, name: str) -> None:
        self._emit({"event": "stage", "name": name})

    def total_known(self, total: int) -> None:
        self._emit({"event": "total_known", "total": total})

    def page_done(self, *, completed: int, total: int | None, cost_usd: float) -> None:
        self._emit(
            {"event": "page_done", "completed": completed, "total": total, "cost_usd": cost_usd}
        )

    def done(self, *, ok: bool, pages_generated: int, cost_usd: float, duration_s: float) -> None:
        self._emit(
            {
                "event": "done",
                "ok": ok,
                "pages_generated": pages_generated,
                "cost_usd": cost_usd,
                "duration_s": duration_s,
            }
        )

    def error(self, message: str) -> None:
        self._emit({"event": "error", "message": message})


# ---------------------------------------------------------------------------
# Completion panels
# ---------------------------------------------------------------------------


def _dead_code_counts(dead_code_report: Any) -> tuple[int, int]:
    """Return ``(unreachable_files, unused_exports)`` from a dead-code report."""
    findings = dead_code_report.findings if dead_code_report else []
    unreachable = sum(1 for f in findings if f.kind.value == "unreachable_file")
    unused = sum(1 for f in findings if f.kind.value == "unused_export")
    return unreachable, unused


def show_full_completion(
    *,
    generated_pages: list,
    decay_count: int,
    decisions_changed: int,
    provider: Any,
    cost: float,
    tokens: int,
    elapsed: float,
) -> None:
    """Render the completion panel for a full (LLM-regenerating) update."""
    metrics: list[tuple[str, str]] = [("Pages updated", str(len(generated_pages)))]
    if decay_count:
        metrics.append(("Pages decayed", str(decay_count)))
    if decisions_changed:
        metrics.append(("Decisions", f"{decisions_changed} changed"))
    if tokens:
        metrics.append(("Total tokens", f"{tokens:,}"))
    if provider is not None:
        metrics.append(("Provider", f"{provider.provider_name} / {provider.model_name}"))
    if cost:
        metrics.append(("Cost", f"${cost:.3f}"))
    metrics.append(("Elapsed", format_elapsed(elapsed)))

    next_steps = [
        ("repowise serve", "browse the updated wiki at localhost:3000"),
        ("repowise search <query>", "search the wiki"),
    ]
    console.print()
    console.print(build_completion_panel("repowise update complete", metrics, next_steps=next_steps))
    console.print()


def show_index_only_completion(
    *,
    graph_builder: Any,
    dead_code_report: Any,
    changed_count: int,
    git_files: int,
    elapsed: float,
) -> None:
    """Render the completion panel for an index-only update (no LLM regen)."""
    graph = graph_builder.graph()
    unreachable, unused = _dead_code_counts(dead_code_report)

    metrics: list[tuple[str, str]] = [
        ("Files changed", str(changed_count)),
        ("Graph", f"{graph.number_of_nodes():,} nodes · {graph.number_of_edges():,} edges"),
        ("Dead code", f"{unreachable} unreachable · {unused} unused exports"),
    ]
    if git_files:
        metrics.append(("Git history", f"{git_files} files refreshed"))
    metrics.append(("Elapsed", format_elapsed(elapsed)))

    next_steps = [
        ("repowise serve", "browse the index at localhost:3000"),
        ("repowise update --docs", "regenerate docs for the changed files"),
    ]
    console.print()
    console.print(
        build_completion_panel("repowise index-only update complete", metrics, next_steps=next_steps)
    )
    console.print()


def show_workspace_completion(
    *,
    ws_name: str,
    updated: int,
    skipped: int,
    errors: int,
    total_files: int,
    total_symbols: int,
    elapsed: float,
) -> None:
    """Render the completion panel for a workspace update."""
    metrics: list[tuple[str, str]] = [
        ("Workspace", ws_name),
        ("Repos updated", str(updated)),
    ]
    if skipped:
        metrics.append(("Skipped", str(skipped)))
    if errors:
        metrics.append(("Errors", str(errors)))
    if total_files:
        metrics.append(("Files", str(total_files)))
    if total_symbols:
        metrics.append(("Symbols", f"{total_symbols:,}"))
    metrics.append(("Elapsed", format_elapsed(elapsed)))

    next_steps = [
        ("repowise status --workspace", "show workspace status"),
        ("repowise serve", "browse a repo wiki at localhost:3000"),
    ]
    console.print()
    console.print(
        build_completion_panel("repowise workspace update complete", metrics, next_steps=next_steps)
    )
    console.print()


# ---------------------------------------------------------------------------
# Verbose detail (opt-in via -v)
# ---------------------------------------------------------------------------


def _render_update_report(
    generated_pages: list,
    affected: Any,
    new_decision_markers: list,
    elapsed: float,
) -> None:
    """Render the detailed generation report table (verbose mode / fallback)."""
    try:
        from repowise.core.generation.report import GenerationReport, render_report

        report = GenerationReport.from_pages(
            generated_pages,
            stale_count=len(affected.decay_only),
            decisions_count=len(new_decision_markers),
            elapsed=elapsed,
        )
        render_report(report, console)
    except Exception:
        # Fallback to simple message if report fails
        console.print(
            f"[bold green]Updated {len(generated_pages)} pages in {elapsed:.1f}s[/bold green]"
        )
