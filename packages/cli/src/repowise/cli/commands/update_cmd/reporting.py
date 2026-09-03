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

    def done(
        self,
        *,
        ok: bool,
        pages_generated: int,
        cost_usd: float,
        duration_s: float,
        degraded: list[str] | None = None,
        outcome: str | None = None,
    ) -> None:
        # ``outcome`` lets a supervising agent tell a real regeneration apart
        # from a run that only deferred to an in-flight update or found nothing
        # to do — all three otherwise emit ``ok=True, pages_generated=0``.
        # Mirrors the ``UpdateOutcome`` values in ``command.py``.
        self._emit(
            {
                "event": "done",
                "ok": ok,
                "outcome": outcome,
                "pages_generated": pages_generated,
                "cost_usd": cost_usd,
                "duration_s": duration_s,
                "degraded": degraded or [],
            }
        )

    def error(self, message: str) -> None:
        self._emit({"event": "error", "message": message})


# ---------------------------------------------------------------------------
# Completion panels
# ---------------------------------------------------------------------------


def render_degraded(degraded: list[str] | None) -> None:
    """Warn about best-effort steps that failed during this update.

    These used to be swallowed (``except Exception: pass``), so the update
    claimed clean success while, say, git metadata or graph nodes silently
    stayed at the previous commit. The run still exits 0, but the panel must
    not say "complete" without this block when something was skipped.

    Not every degraded step heals on the next update. A transient failure
    (lock contention, a server that recovered, a network blip) re-runs and
    fixes itself, so promising a retry is honest. A step that failed because
    the *config* is broken — the canonical case is the embedder: a bad
    ``REPOWISE_EMBEDDER`` value, a missing/bad API key, or an unreachable
    endpoint the user configured — reads the same config on the next run and
    fails identically, so telling the user "will retry on the next update" is
    a promise nothing will keep. Those steps are surfaced as a config error
    with the way to fix them instead.
    """
    if not degraded:
        return
    config, retryable = _split_degraded(degraded)
    if retryable:
        console.print()
        console.print(
            f"[yellow]Update completed with {len(retryable)} degraded step(s) "
            "(will retry on the next update):[/yellow]"
        )
        for entry in retryable:
            console.print(f"  [yellow]-[/yellow] {entry}")
    if config:
        console.print()
        console.print(
            "[yellow]Update degraded on config-dependent steps a retry cannot heal. "
            "Fix the config, then run [cyan]repowise reindex[/cyan]:[/yellow]"
        )
        for entry in config:
            console.print(f"  [yellow]-[/yellow] {entry}")


def _split_degraded(
    degraded: list[str],
) -> tuple[list[str], list[str]]:
    """Split degraded entries into ``(config_cannot_self_heal, retryable)``.

    Entries are ``"Step: message"`` strings. The step name before the first
    colon decides the class.  The embedder step is config-driven: a bad env
    value or unreachable endpoint is identical on the next run, so no retry
    can heal it and the panel must say how to fix the config instead of
    promising a retry.  Every other step is a transient/range-scoped failure
    that genuinely re-runs on the next update.
    """
    config: list[str] = []
    retryable: list[str] = []
    for entry in degraded:
        step = entry.split(":", 1)[0].strip()
        if step in _CONFIG_CANNOT_SELF_HEAL_STEPS:
            config.append(entry)
        else:
            retryable.append(entry)
    return config, retryable


# Degraded steps whose failure is a config value that is unchanged on the
# next run, so "will retry on the next update" would be a promise retry cannot
# keep. Promoted here so the panel can say how to fix them instead.
_CONFIG_CANNOT_SELF_HEAL_STEPS = frozenset({"Page embedding"})


def _dead_code_counts(
    dead_code_report: Any, changed_paths: list[str] | None = None
) -> tuple[int, int]:
    """Return ``(unreachable_files, unused_exports)`` from a dead-code report.

    The report is repo-wide, but this panel is a summary of the update that
    just ran, so the counts stay scoped to the files it touched. Reporting the
    repo-wide totals here would turn a one-file update on a large repo into
    "Dead code  759 unreachable" where it previously said 0, which reads as
    the update having caused it.
    """
    findings = dead_code_report.findings if dead_code_report else []
    if changed_paths is not None:
        scope = set(changed_paths)
        findings = [f for f in findings if f.file_path in scope]
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
    degraded: list[str] | None = None,
) -> None:
    """Render the completion panel for a full (LLM-regenerating) update."""
    render_degraded(degraded)
    metrics: list[tuple[str, str]] = [("Pages updated", str(len(generated_pages)))]
    if degraded:
        metrics.append(("Degraded", f"{len(degraded)} step(s)"))
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
    console.print(
        build_completion_panel("repowise update complete", metrics, next_steps=next_steps)
    )
    console.print()


def show_index_only_completion(
    *,
    graph_builder: Any,
    dead_code_report: Any,
    changed_count: int,
    git_files: int,
    elapsed: float,
    degraded: list[str] | None = None,
    pages_rendered: int = 0,
    template_wiki: bool = False,
    changed_paths: list[str] | None = None,
) -> None:
    """Render the completion panel for an index-only update (no LLM regen).

    *pages_rendered* is non-zero on a repo whose wiki was rendered from
    templates: those pages are re-rendered here for free, so the panel says so
    rather than leaving the user to assume the wiki went stale.
    """
    render_degraded(degraded)
    graph = graph_builder.graph()
    unreachable, unused = _dead_code_counts(dead_code_report, changed_paths)

    metrics: list[tuple[str, str]] = [
        ("Files changed", str(changed_count)),
        ("Graph", f"{graph.number_of_nodes():,} nodes · {graph.number_of_edges():,} edges"),
        ("Dead code", f"{unreachable} unreachable · {unused} unused exports"),
    ]
    if pages_rendered:
        metrics.append(("Wiki pages", f"{pages_rendered} re-rendered from structure"))
    if degraded:
        metrics.append(("Degraded", f"{len(degraded)} step(s)"))
    if git_files:
        metrics.append(("Git history", f"{git_files} files refreshed"))
    metrics.append(("Elapsed", format_elapsed(elapsed)))

    next_steps = [
        ("repowise serve", "browse the index at localhost:3000"),
    ]
    if template_wiki:
        # The scoped, cost-gated upgrade path — a coverage, a directory or one
        # page at a time — not the all-or-nothing `update --full`.
        next_steps.append(
            ("repowise generate", "upgrade the wiki to model-written prose (needs a key)")
        )
    else:
        next_steps.append(("repowise update --docs", "regenerate docs for the changed files"))
    console.print()
    console.print(
        build_completion_panel(
            "repowise index-only update complete", metrics, next_steps=next_steps
        )
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
    detail: bool = False,
) -> None:
    """Render the generation report.

    The quality checks print on every run; ``detail`` adds the page, token and
    cost statistics on top. Checks used to render only under ``-v``, and since
    ``repowise.core`` logging is silenced outside verbose mode, that left a
    flagged page pair with no channel at all — it was measured, reported to
    nobody, and read as a clean run.
    """
    try:
        from repowise.core.generation.report import (
            GenerationReport,
            render_generation_checks,
            render_report,
        )

        report = GenerationReport.from_pages(
            generated_pages,
            stale_count=len(affected.decay_only),
            decisions_count=len(new_decision_markers),
            elapsed=elapsed,
        )
        if detail:
            render_report(report, console)
        else:
            render_generation_checks(report, console)
    except Exception as exc:
        # Never fail a completed index over its own summary, but never let the
        # failure pass for a clean run either. This message is the only signal
        # that the checks below it are missing rather than empty.
        console.print(
            f"[bold red]Generation checks did not run:[/bold red] {type(exc).__name__}: {exc}"
        )
