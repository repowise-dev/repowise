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
    ERR,
    OK,
    WARN,
    build_completion_panel,
    build_contextual_next_steps,
    build_status_notes,
    format_elapsed,
    keyless_upgrade_note,
    mascot,
    print_files_written,
)
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mode_names import (
    INDEX_ONLY,
    TITLE_WORKSPACE,
    completion_title,
    pages,
    scope_line,
)
from repowise.core.index_scope import FAST_TO_FULL_COMMAND, fast_to_full_command


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
    owl: str = mascot.DONE,
) -> None:
    """Render the final completion panel (index-only or full mode).

    *owl* is the mini owl state on the panel title: how the run finished.

    *setup* is the :class:`~repowise.cli.editor_setup.EditorSetupOutcome`
    snapshot, so the next-step panel and the MCP status note reflect what setup
    actually did. Passed as ``None`` only by callers with nothing to report.

    *files_written* is what editor setup put in the working tree, straight from
    the writers. Printed below the panel and dim: it is a receipt, and the run's
    result is the panel above it.
    """
    elapsed = time.monotonic() - start
    from repowise.core.index_scope import resolve_index_scope

    _scope = resolve_index_scope(load_state(Path(repo_path)), load_config(Path(repo_path)))

    # Unavailable also describes a keyless run; only a failed write earns
    # the failure line.
    _embed_failed = getattr(result, "embed_failed_pages", 0)
    _semantic_on = _scope["search"]["semantic"] == "available"

    _graph_final = result.graph_builder.graph()
    _dc_unreachable = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unreachable_file"
    )
    _dc_unused = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unused_export"
    )
    _n_decisions = sum(result.decision_report.by_source.values()) if result.decision_report else 0
    _hotspot_count_final = (
        result.git_summary.hotspots
        if result.git_summary and hasattr(result.git_summary, "hotspots")
        else 0
    )

    # Find top hotspot file for contextual next steps
    _top_hotspot = ""
    if result.git_meta_map:
        _by_churn = sorted(
            result.git_meta_map.items(),
            key=lambda x: x[1].get("commit_count", 0),
            reverse=True,
        )
        if _by_churn:
            _top_hotspot = _by_churn[0][0]
            # Shorten to basename for display
            if "/" in _top_hotspot:
                _top_hotspot = _top_hotspot.rsplit("/", 1)[-1]

    # Build a compact language summary for the completion panel
    _lang_dist_final = result.repo_structure.root_language_distribution
    if _lang_dist_final:
        _top_final = sorted(_lang_dist_final.items(), key=lambda x: -x[1])[:4]
        _lang_summary_final = ", ".join(f"{lang} {pct:.0%}" for lang, pct in _top_final)
        if len(_lang_dist_final) > 4:
            _lang_summary_final += f" +{len(_lang_dist_final) - 4} more"
    else:
        _lang_summary_final = str(len(result.languages))

    # What the run found, in both modes: this panel is the one place a run
    # reports file count, symbols, languages and graph size.
    _index_rows: list[tuple[str, str]] = [
        ("Scope", scope_line(_scope)),
        ("Files indexed", f"{result.file_count:,}"),
        ("Symbols", f"{result.symbol_count:,}"),
        ("Languages", _lang_summary_final),
    ]
    if _scope["file_pages"]["eligible"] is not None:
        _fp = _scope["file_pages"]
        _index_rows.append(
            (
                "File pages",
                f"{_fp['generated']}/{_fp['eligible']} generated · {_fp['omitted']} omitted",
            )
        )
    _structure_rows: list[tuple[str, str]] = [
        (
            "Graph",
            f"{_graph_final.number_of_nodes():,} nodes · {_graph_final.number_of_edges():,} edges",
        ),
        ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
        ("Decisions", str(_n_decisions)),
    ]
    if result.git_summary:
        _structure_rows.append(
            (
                "Git history",
                f"{result.git_summary.files_indexed:,} files · {_hotspot_count_final} hotspots",
            )
        )

    if effective_index_only:
        _template_pages = len(result.generated_pages or [])
        metrics: list[tuple[str, str]] = [
            *_index_rows,
            (
                "Documentation",
                f"{pages(_template_pages)} built from code" if _template_pages else "none",
            ),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            *_structure_rows,
        ]

        next_steps = build_contextual_next_steps(
            index_only=True,
            fast_mode=(run_mode == "fast"),
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
            setup=setup,
        )
        console.print()
        console.print(
            build_completion_panel(
                completion_title(_scope), metrics, next_steps=next_steps, owl=owl
            )
        )
        console.print()
        # Fast mode reaches this branch too, and it generates nothing, so the
        # note has to check rather than assume.
        if result.generated_pages:
            # Read from the stamped scope, so the note cannot contradict what
            # status and MCP report for the same run.
            if _embed_failed:
                _search_note = "Full-text\n  search works now."
            elif _semantic_on:
                _search_note = "Full-text\n  and semantic search work now."
            else:
                _search_note = (
                    "Full-text\n  search works now; semantic search needs an embedder "
                    "(Ollama is the keyless one)."
                )
            console.print(
                "  [dim]Every page is built from code and says so in its footer. "
                f"{_search_note}[/dim]"
            )
            # A keyless index is finished, not partial — this just names the
            # two upgrades available from here and the command for each, so
            # the headless path (no editor setup, no `generate` row in
            # build_contextual_next_steps) still learns about them (#3105).
            _note = keyless_upgrade_note(_semantic_on, _scope["search"]["next_command"])
            console.print(f"  [dim]{_note}[/dim]")
            _print_embed_failure(_embed_failed)
        else:
            console.print(_fast_to_full_note())
        for _line in build_status_notes(setup):
            console.print(_line)
        console.print()
        _render_defect_accuracy(result)
    else:
        _pages = result.generated_pages or []
        _failed_ids = getattr(result, "failed_page_ids", []) or []
        total_tokens = sum(p.total_tokens for p in _pages)
        # Split model-written from the zero-LLM deterministic coverage tail so
        # broad coverage reads as thoroughness, not padding.
        # A stub standing in for a failed provider call is in ``_pages`` like
        # any other page, so counting the list here reported it as generated
        # while the same run reported it as failed one line down.
        from repowise.core.generation.models import count_stub_fallbacks

        _stubs = count_stub_fallbacks(_pages)
        _written = len(_pages) - _stubs
        _det = sum(1 for p in _pages if getattr(p, "provider_name", "") == "template")
        _ai = len(_pages) - _det
        _pages_label = pages(_written)
        if _failed_ids:
            _pages_label += f" ({len(_failed_ids):,} failed)"
        elif _det:
            _pages_label += f" ({_ai:,} written by the model · {_det:,} built from code)"
        # The ledger has the real figure; before this the panel reported
        # millions of tokens and no dollars, on a run the user had just
        # approved a dollar estimate for.
        _cost_usd = getattr(result, "llm_cost_usd", None)
        _spend_row = (
            ("Cost", f"${_cost_usd:.2f}  [dim]({total_tokens:,} tokens)[/dim]")
            if _cost_usd is not None
            else ("Total tokens", f"{total_tokens:,}")
        )
        metrics = [
            *_index_rows,
            ("Documentation", _pages_label),
            _spend_row,
            ("Provider", f"{provider.provider_name} / {provider.model_name}"),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            *_structure_rows,
        ]

        next_steps = build_contextual_next_steps(
            index_only=False,
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
            setup=setup,
        )

        console.print()
        console.print(
            build_completion_panel(
                completion_title(_scope), metrics, next_steps=next_steps, owl=owl
            )
        )
        console.print()
        _print_embed_failure(_embed_failed)
        if not _semantic_on and not _embed_failed and _scope["search"]["next_command"]:
            console.print(
                "  [dim]Semantic search is off. Run "
                f"[bold]{_scope['search']['next_command']}[/bold] to turn it on.[/dim]"
            )
        _render_defect_accuracy(result)
        # A concise, dynamic MCP note (who is connected, how others connect)
        # replaces the old wall of manual per-client config: init already wrote
        # the Claude Code / VS Code registrations and repo `.mcp.json`.
        for _line in build_status_notes(setup):
            console.print(_line)
        console.print()

    # One quiet stderr line about repowise.dev (see hints.py for when it is
    # shown); a big repo hears about staying fresh instead.
    from repowise.cli.hints import maybe_hint

    maybe_hint(completion_hint(run_mode, result.file_count))

    print_files_written(console, Path(repo_path), files_written or [])

    _show_generation_checks(result)


def _print_embed_failure(failed: int) -> None:
    if failed:
        console.print(
            f"  [{WARN}]Semantic search is unavailable:[/] embedding failed for "
            f"{failed} page(s). See the warning above, then run "
            "[bold]repowise reindex[/bold]."
        )


def _show_generation_checks(result: Any) -> None:
    """Report the generation quality checks on the pages this run wrote.

    ``init`` built no report at all, so the checks ran on a later ``update``
    and never on the first index — which is the run that creates a repository's
    pages, and so the run where duplication first appears.

    Only the checks are shown, not the statistics table: the completion panel
    above already carries the page count, cost and token totals.
    """
    try:
        from repowise.core.generation.report import GenerationReport, render_generation_checks

        render_generation_checks(GenerationReport.from_pages(result.generated_pages or []), console)
    except Exception as exc:
        # A first index that could not check itself must not read like one that
        # checked itself clean. The run still exits 0; the wiki is written.
        console.print(f"[{ERR}]Generation checks did not run:[/] {type(exc).__name__}: {exc}")


def _fast_to_full_note() -> str:
    """The closing note of an Index only run, naming the one fast-to-full command."""
    return (
        f"  [dim]{INDEX_ONLY}: fast mode indexes the graph and git history, no "
        f"documentation.\n  Run [bold]{FAST_TO_FULL_COMMAND}[/bold] to build it from "
        "code (no key needed).[/dim]"
    )


def _workspace_next_steps(run_mode: str, keyless: bool) -> list[tuple[str, str]]:
    """Dashboard and status, then the one upgrade that fits how the repos were indexed."""
    if run_mode == "fast":
        upgrade = (
            fast_to_full_command("<repo>"),
            "build a repo's documentation from code and its full git history (no key)",
        )
    elif keyless:
        upgrade = ("repowise generate <repo>", "write a repo's documentation with a model")
    else:
        upgrade = ("repowise search <query>", "search across all indexed repos")
    return [
        ("repowise serve", "open the workspace dashboard at http://localhost:3000"),
        ("repowise status --workspace", "show workspace status"),
        upgrade,
    ]


def _upgradable_skips(docs_outcomes: dict[str, tuple[int, str | None]]) -> list[tuple[str, str]]:
    """Repos that skipped documentation; a dry run wrote nothing, so it has nothing to upgrade."""
    return [
        (alias, reason)
        for alias, (_count, reason) in docs_outcomes.items()
        if reason and reason != "dry run"
    ]


def show_workspace_completion(
    *,
    selected: list[Any],
    errors: list,
    total_files: int,
    total_symbols: int,
    total_pages: int,
    primary_alias: str,
    elapsed: float,
    index_only: bool,
    provider: Any,
    docs_outcomes: dict[str, tuple[int, str | None]],
    run_mode: str = "standard",
) -> None:
    """Render the workspace completion panel + per-repo docs status."""
    metrics: list[tuple[str, str]] = [
        ("Repositories", f"{len(selected) - len(errors)} indexed"),
        ("Total files", f"{total_files:,}"),
        ("Total symbols", f"{total_symbols:,}"),
        ("Primary repo", primary_alias),
        ("Elapsed", format_elapsed(elapsed)),
    ]
    if not index_only and provider is not None:
        metrics.insert(3, ("Documentation", pages(total_pages)))
        metrics.insert(4, ("Provider", f"{provider.provider_name} / {provider.model_name}"))
    if errors:
        metrics.append(("Errors", f"{len(errors)} repos failed"))

    next_steps = _workspace_next_steps(run_mode, index_only or provider is None)

    console.print()
    console.print(
        build_completion_panel(
            TITLE_WORKSPACE,
            metrics,
            next_steps=next_steps,
            owl=mascot.WARNED if errors else mascot.DONE,
        )
    )
    console.print()

    # Honest docs status — print a per-repo summary listing exactly which
    # repos generated pages and which were skipped, so the user never has
    # to discover empty Docs/Overview in the web UI on their own.
    docs_skipped = _upgradable_skips(docs_outcomes)
    if docs_outcomes:
        console.print("[bold]Documentation[/bold]")
        for alias, (count, reason) in docs_outcomes.items():
            if reason:
                console.print(
                    f"  [{WARN}]{glyph('fail')}[/] {alias:<20} [{WARN}]skipped[/]  "
                    f"[dim]({reason})[/dim]"
                )
            else:
                console.print(f"  [{OK}]{glyph('ok')}[/] {alias:<20} [{OK}]{count} pages[/]")
        if docs_skipped:
            first = docs_skipped[0][0]
            console.print()
            console.print(
                f"  Run [bold]{fast_to_full_command(first)}[/bold] "
                "to build documentation for a skipped repo."
            )
        console.print()
