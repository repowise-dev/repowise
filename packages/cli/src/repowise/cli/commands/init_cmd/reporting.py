"""Console rendering for ``repowise init`` — analysis + completion panels.

Pure presentation: every function takes a finished :class:`PipelineResult` (or
workspace tallies) and prints Rich panels. No persistence or generation work
happens here.
"""

from __future__ import annotations

import time
from typing import Any

from repowise.cli.helpers import console
from repowise.cli.ui import (
    build_analysis_summary_panel,
    build_completion_panel,
    build_contextual_next_steps,
    format_elapsed,
)
from repowise.cli.ui.mascot import EYES_HAPPY, mini

# Happy owl prefix for completion panel titles, e.g. "{^ ^}  repowise init complete".
_HAPPY = mini(EYES_HAPPY)


def show_analysis_summary(result: Any) -> None:
    """Render the analysis-complete interstitial shown before generation."""
    _graph = result.graph_builder.graph()
    _dc_unreachable_pre = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unreachable_file"
    )
    _dc_unused_pre = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unused_export"
    )
    _dc_lines_pre = result.dead_code_report.deletable_lines if result.dead_code_report else 0
    _n_decisions_pre = (
        sum(result.decision_report.by_source.values()) if result.decision_report else 0
    )
    _lang_dist = result.repo_structure.root_language_distribution
    _lang_summary = ""
    if _lang_dist:
        _top = sorted(_lang_dist.items(), key=lambda x: -x[1])[:4]
        _lang_summary = ", ".join(f"{lang} {pct:.0%}" for lang, pct in _top)
        if len(_lang_dist) > 4:
            _lang_summary += f" +{len(_lang_dist) - 4} more"

    # Community count (best-effort)
    _community_count = 0
    try:
        if hasattr(result.graph_builder, "communities"):
            _community_count = len(result.graph_builder.communities())
    except Exception:
        pass

    console.print()
    console.print(
        build_analysis_summary_panel(
            file_count=result.file_count,
            symbol_count=result.symbol_count,
            graph_nodes=_graph.number_of_nodes(),
            graph_edges=_graph.number_of_edges(),
            dead_unreachable=_dc_unreachable_pre,
            dead_unused=_dc_unused_pre,
            dead_lines=_dc_lines_pre,
            decision_count=_n_decisions_pre,
            git_files=result.git_summary.files_indexed if result.git_summary else 0,
            hotspot_count=result.git_summary.hotspots
            if result.git_summary and hasattr(result.git_summary, "hotspots")
            else 0,
            community_count=_community_count,
            lang_summary=_lang_summary,
        )
    )


def show_completion(
    *,
    repo_path: Any,
    result: Any,
    start: float,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
) -> None:
    """Render the final completion panel (index-only or full mode)."""
    elapsed = time.monotonic() - start

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

    if effective_index_only:
        metrics: list[tuple[str, str]] = [
            ("Files indexed", str(result.file_count)),
            ("Symbols", f"{result.symbol_count:,}"),
            ("Languages", _lang_summary_final),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            (
                "Graph",
                f"{_graph_final.number_of_nodes()} nodes · {_graph_final.number_of_edges()} edges",
            ),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if result.git_summary:
            metrics.append(
                (
                    "Git history",
                    f"{result.git_summary.files_indexed} files · {_hotspot_count_final} hotspots",
                )
            )

        next_steps = build_contextual_next_steps(
            index_only=True,
            fast_mode=(run_mode == "fast"),
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
        )
        console.print()
        console.print(
            build_completion_panel(
                f"{_HAPPY}  repowise index complete", metrics, next_steps=next_steps
            )
        )
        console.print()
    else:
        total_tokens = sum(p.total_tokens for p in (result.generated_pages or []))
        metrics = [
            ("Pages generated", str(len(result.generated_pages or []))),
            ("Total tokens", f"{total_tokens:,}"),
            ("Provider", f"{provider.provider_name} / {provider.model_name}"),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if result.git_summary:
            metrics.append(
                (
                    "Git history",
                    f"{result.git_summary.files_indexed} files · {_hotspot_count_final} hotspots",
                )
            )

        next_steps = build_contextual_next_steps(
            index_only=False,
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
        )

        from repowise.cli.mcp_config import format_setup_instructions

        console.print()
        console.print(
            build_completion_panel(
                f"{_HAPPY}  repowise init complete", metrics, next_steps=next_steps
            )
        )
        console.print()
        console.print(format_setup_instructions(repo_path))
        console.print()


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
) -> None:
    """Render the workspace completion panel + per-repo docs status."""
    metrics: list[tuple[str, str]] = [
        ("Repositories", f"{len(selected) - len(errors)} indexed"),
        ("Total files", str(total_files)),
        ("Total symbols", f"{total_symbols:,}"),
        ("Primary repo", primary_alias),
        ("Elapsed", format_elapsed(elapsed)),
    ]
    if not index_only and provider is not None:
        metrics.insert(3, ("Pages generated", str(total_pages)))
        metrics.insert(4, ("Provider", f"{provider.provider_name} / {provider.model_name}"))
    if errors:
        metrics.append(("Errors", f"{len(errors)} repos failed"))

    if index_only or provider is None:
        next_steps = [
            ("repowise mcp <repo-path>", "start MCP server for a repo"),
            ("repowise status --workspace", "show workspace status"),
            ("repowise init <repo> --provider gemini", "generate full docs for a repo"),
        ]
    else:
        next_steps = [
            ("repowise mcp <repo-path>", "start MCP server for a repo"),
            ("repowise status --workspace", "show workspace status"),
            ("repowise search <query>", "search across all indexed repos"),
        ]

    console.print()
    console.print(
        build_completion_panel(
            f"{_HAPPY}  repowise workspace init complete", metrics, next_steps=next_steps
        )
    )
    console.print()

    # Honest docs status — print a per-repo summary listing exactly which
    # repos generated pages and which were skipped, so the user never has
    # to discover empty Docs/Overview in the web UI on their own.
    docs_skipped = [(alias, reason) for alias, (count, reason) in docs_outcomes.items() if reason]
    if docs_outcomes:
        console.print("[bold]Docs status[/bold]")
        for alias, (count, reason) in docs_outcomes.items():
            if reason:
                console.print(
                    f"  [yellow]✗[/yellow] {alias:<20} [yellow]skipped[/yellow]  [dim]({reason})[/dim]"
                )
            else:
                console.print(f"  [green]✓[/green] {alias:<20} [green]{count} pages[/green]")
        if docs_skipped:
            first = docs_skipped[0][0]
            console.print()
            console.print(
                f"  Run [bold]repowise update --repo {first} --docs[/bold] "
                "to generate docs for a skipped repo."
            )
        console.print()
