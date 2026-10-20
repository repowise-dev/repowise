"""Console rendering for ``repowise init`` — analysis + completion panels.

Pure presentation: every function takes a finished :class:`PipelineResult` (or
workspace tallies) and prints Rich panels. No persistence or generation work
happens here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, load_config, load_state
from repowise.cli.ui import (
    build_contextual_next_steps,
    format_elapsed,
    mascot,
)
from repowise.cli.ui.finish_panel import FinishFacts, print_finish_panel
from repowise.cli.ui.mode_names import pages, provider_model
from repowise.cli.ui.workspace_finish import (
    RepoOutcome,
    WorkspaceFacts,
    print_workspace_finish_panel,
)


def _dead_counts(result: Any) -> tuple[int, int]:
    findings = result.dead_code_report.findings if result.dead_code_report else []
    kinds = [f.kind.value for f in findings]
    return kinds.count("unreachable_file"), kinds.count("unused_export")


def _lang_summary(result: Any) -> str:
    dist = result.repo_structure.root_language_distribution
    if not dist:
        return ""
    top = sorted(dist.items(), key=lambda x: -x[1])[:4]
    summary = ", ".join(f"{lang} {pct:.0%}" for lang, pct in top)
    if len(dist) > 4:
        summary += f" +{len(dist) - 4} more"
    return summary


def _hotspots(result: Any) -> int:
    summary = result.git_summary
    return summary.hotspots if summary and hasattr(summary, "hotspots") else 0


def _render_defect_accuracy(result: Any) -> None:
    """Print a one-line "does the score find the bugs?" validation callout.

    Stays silent unless the health analysis ran and the repo has enough files
    and recent bug-fix history for an honest number (the core compute returns
    ``None`` otherwise).
    """
    report = getattr(result, "health_report", None)
    if report is None:
        return
    try:
        from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
        from repowise.core.analysis.health.ranking import deduction_by_path

        stat = compute_defect_accuracy(
            report.metrics,
            report.findings,
            deductions=deduction_by_path(report.findings),
        )
    except Exception:
        return
    if not stat:
        return

    months = max(1, round(stat["window_days"] / 30))
    window = "month" if months == 1 else f"{months} months"
    line = (
        f"  [dim]Health check: [/dim]"
        f"[bold]{stat['hits']}/{stat['k']}[/bold]"
        f"[dim] lowest-health files had a bug fix in the last {window}[/dim]"
    )
    if stat.get("lift") is not None:
        base_pct = round(stat["base_rate"] * 100)
        line += f"[dim], [/dim][bold]{stat['lift']}x[/bold][dim] the {base_pct}% baseline.[/dim]"
    else:
        line += "[dim].[/dim]"
    console.print(line)
    console.print()


#: From this many files, the hosted pitch is staying fresh without a long local
#: run. Lower than the fast-mode offer on purpose: a hint costs nothing to skip.
_LARGE_REPO_FILES = 1000


def completion_hint(run_mode: str, file_count: int) -> str:
    """Which hint a finished init earns: big repos hear about freshness."""
    return "large_repo" if run_mode == "fast" or file_count >= _LARGE_REPO_FILES else "init_success"


def show_completion(
    *,
    repo_path: Any,
    result: Any,
    start: float,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
    setup: Any = None,
    files_written: list[Path] | None = None,
    verbose: bool = False,
    owl: str = mascot.DONE,
) -> list[tuple[str, str]]:
    """Render the one finish panel and return its next steps; generation checks only under ``-v``.

    *owl* is the mini owl state on the panel title: how the run finished.

    *setup* is the :class:`~repowise.cli.editor_setup.EditorSetupOutcome`
    snapshot and *files_written* what editor setup put in the working tree,
    so the panel reports what setup actually did.
    """
    from repowise.core.index_scope import resolve_index_scope

    scope = resolve_index_scope(load_state(Path(repo_path)), load_config(Path(repo_path)))
    graph = result.graph_builder.graph()
    dead_unreachable, dead_unused = _dead_counts(result)
    decisions = sum(result.decision_report.by_source.values()) if result.decision_report else 0
    # A most-committed file is only worth naming when git analysis calls it a hotspot.
    top_hotspot = _top_hotspot(result) if _hotspots(result) else ""
    documentation, spend = (
        _template_pages(result) if effective_index_only else _model_pages(result)
    )
    facts = FinishFacts(
        scope=scope,
        files=result.file_count,
        symbols=result.symbol_count,
        languages=_lang_summary(result) or str(len(getattr(result, "languages", []))),
        graph_nodes=graph.number_of_nodes(),
        graph_edges=graph.number_of_edges(),
        elapsed=format_elapsed(time.monotonic() - start),
        dead_unreachable=dead_unreachable,
        dead_unused=dead_unused,
        decisions=decisions,
        git_files=result.git_summary.files_indexed if result.git_summary else None,
        hotspots=_hotspots(result),
        top_hotspot=top_hotspot,
        pages=documentation,
        spend=spend,
        embed_failed=getattr(result, "embed_failed_pages", 0),
    )
    steps = build_contextual_next_steps(
        index_only=effective_index_only,
        fast_mode=(run_mode == "fast"),
        dead_unreachable=dead_unreachable,
        dead_unused=dead_unused,
        hotspot_count=facts.hotspots,
        decision_count=decisions,
        top_hotspot=top_hotspot,
        setup=setup,
    )
    console.print()
    print_finish_panel(
        console,
        facts,
        steps=steps,
        setup=setup,
        repo_path=Path(repo_path),
        files_written=files_written,
        owl=owl,
    )
    console.print()
    _render_defect_accuracy(result)

    # One quiet stderr line about repowise.dev (see hints.py for when it is
    # shown); a big repo hears about staying fresh instead.
    from repowise.cli.hints import maybe_hint

    maybe_hint(completion_hint(run_mode, result.file_count))

    if verbose:
        _show_generation_checks(result)
    return steps


def _top_hotspot(result: Any) -> str:
    """Basename of the most-committed file, for the risk step and the agent question."""
    if not result.git_meta_map:
        return ""
    path = max(result.git_meta_map.items(), key=lambda x: x[1].get("commit_count", 0))[0]
    return path.rsplit("/", 1)[-1]


def _template_pages(result: Any) -> tuple[str | None, None]:
    count = len(result.generated_pages or [])
    return (pages(count) if count else None), None


def _model_pages(result: Any) -> tuple[str, str]:
    """Pages and spend for a model run; a stub left by a failed call counts as failed."""
    from repowise.core.generation.models import count_stub_fallbacks

    generated = result.generated_pages or []
    failed = getattr(result, "failed_page_ids", []) or []
    written = len(generated) - count_stub_fallbacks(generated)
    from_code = sum(1 for p in generated if getattr(p, "provider_name", "") == "template")
    label = pages(written)
    if failed:
        label += f" ({len(failed)} failed)"
    elif from_code:
        label += f" ({len(generated) - from_code:,} written by the model · "
        label += f"{from_code:,} built from code)"
    tokens = sum(p.total_tokens for p in generated)
    cost = getattr(result, "llm_cost_usd", None)
    spend = f"${cost:.2f} ({tokens:,} tokens)" if cost is not None else f"{tokens:,} tokens"
    return label, spend


def _show_generation_checks(result: Any) -> None:
    """The quality checks on the pages this run wrote (``-v`` only)."""
    try:
        from repowise.core.generation.report import GenerationReport, render_generation_checks

        render_generation_checks(GenerationReport.from_pages(result.generated_pages or []), console)
    except Exception as exc:
        # The run still exits 0 and the documentation is written.
        console.print(f"  [dim]Generation checks were skipped ({type(exc).__name__}: {exc}).[/dim]")


def show_workspace_completion(
    *,
    selected: list[Any],
    errors: list,
    total_files: int,
    total_symbols: int,
    primary_alias: str,
    elapsed: float,
    index_only: bool,
    provider: Any,
    docs_outcomes: dict[str, tuple[int, str | None]],
    dry_run: bool = False,
    setup: Any = None,
) -> None:
    """Render the workspace finish panel, the per-repo documentation inside it."""
    from repowise.core.index_scope import resolve_index_scope

    failed = dict(errors)
    repos = []
    for repo in selected:
        if repo.alias in failed:
            repos.append(RepoOutcome(repo.alias, error=failed[repo.alias]))
            continue
        count, skipped = docs_outcomes.get(repo.alias, (0, None))
        scope = resolve_index_scope(load_state(repo.path), load_config(repo.path))
        repos.append(RepoOutcome(repo.alias, scope, pages=count, skipped=skipped))
    facts = WorkspaceFacts(
        repos=repos,
        files=total_files,
        symbols=total_symbols,
        primary=primary_alias,
        dry_run=dry_run,
        elapsed=format_elapsed(elapsed),
        provider=None
        if index_only or provider is None
        else provider_model(provider.provider_name, provider.model_name),
    )
    console.print()
    print_workspace_finish_panel(
        console, facts, setup=setup, owl=mascot.WARNED if errors else mascot.DONE
    )
    console.print()
