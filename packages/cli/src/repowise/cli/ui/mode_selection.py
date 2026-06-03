"""Interactive mode selection, fast-mode offer, and advanced configuration."""

from __future__ import annotations

import os
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from repowise.cli.ui.brand import BRAND, BRAND_STYLE, DIM
from repowise.cli.ui.repo_scanner import RepoScanInfo
from repowise.core.reasoning import REASONING_MODES

# A repo at or above this many files is "large" — large enough that a quick
# fast-mode first index (graph + essential git, no LLM docs) is worth offering.
LARGE_REPO_FILE_THRESHOLD = 5000


def should_offer_fast_mode(scan: RepoScanInfo | None) -> bool:
    """Whether to surface the fast-mode offer for this repo.

    Fast mode only makes sense on large repos; small repos run full in seconds
    so the offer would just be noise.
    """
    return scan is not None and scan.total_files > LARGE_REPO_FILE_THRESHOLD


def interactive_fast_mode_offer(
    console: Console,
    scan: RepoScanInfo | None,
    *,
    default_fast: bool,
) -> bool:
    """Offer fast mode after a large repo is detected. Returns True to use it.

    Shown only when :func:`should_offer_fast_mode` is true. Fast mode is a quick
    first index (dependency graph + essential git history, metrics in SQL) with
    no per-file blame, no co-change walk, and no LLM docs — backfillable later.
    """
    n = scan.total_files if scan else 0
    body = Text()
    body.append("  Large repository detected — ", style="bold")
    body.append(f"{n:,} files.\n\n", style=BRAND_STYLE)
    body.append("  Fast mode runs a quick first index:\n", style="bold")
    body.append("    • dependency graph + essential git history\n")
    body.append("    • graph metrics materialized to SQL\n")
    body.append("    • no per-file blame, no co-change walk, no LLM docs\n\n")
    body.append("  You can backfill full git history and generate docs later.\n", style="dim")
    console.print(
        Panel(
            body,
            title="[bold]Fast first index?[/bold]",
            border_style=BRAND,
            padding=(1, 2),
        )
    )
    return click.confirm("  Use fast mode?", default=default_fast)


def interactive_mode_select(console: Console) -> str:
    """Let the user choose full / index-only / advanced.

    Returns ``"full"``, ``"index_only"``, or ``"advanced"``.
    """
    body = Text()
    body.append("  [1]", style=BRAND_STYLE)
    body.append("  Full documentation  ", style="bold")
    body.append("(recommended)\n", style="dim")
    body.append("       Generate wiki pages, architecture diagrams, and API\n")
    body.append("       docs using an AI provider.\n\n")

    body.append("  [2]", style=BRAND_STYLE)
    body.append("  Index only  ", style="bold")
    body.append("(no LLM, no cost)\n", style="dim")
    body.append("       Dependency graph, git history, dead code analysis.\n")
    body.append("       Perfect for MCP-powered AI coding assistants.\n\n")

    body.append("  [3]", style=BRAND_STYLE)
    body.append("  Advanced\n", style="bold")
    body.append("       Full documentation with extra configuration\n")
    body.append("       (commit limit, exclude patterns, concurrency …)")

    console.print(
        Panel(
            body,
            title="[bold]How would you like to document this repo?[/bold]",
            border_style=BRAND,
            padding=(1, 2),
        )
    )

    choice = Prompt.ask(
        "  Select mode",
        choices=["1", "2", "3"],
        default="1",
        console=console,
    )
    return {"1": "full", "2": "index_only", "3": "advanced"}[choice]


def _prompt_scope(console: Console, scan: RepoScanInfo | None, result: dict[str, Any]) -> None:
    """Scope section: which file classes to include."""
    console.print()
    console.print(f"  [{BRAND}]Scope[/]")
    console.print("  [dim]Choose what to include in the analysis[/dim]")
    console.print()

    test_hint = f" ({scan.test_file_count:,} found)" if scan and scan.test_file_count else ""
    result["skip_tests"] = click.confirm(
        f"  Skip test files?{test_hint}",
        default=False,
    )

    infra_hint = f" ({scan.infra_file_count:,} found)" if scan and scan.infra_file_count else ""
    result["skip_infra"] = click.confirm(
        f"  Skip infrastructure files?{infra_hint} (Dockerfile, CI, Makefile …)",
        default=False,
    )

    if scan and scan.submodule_count:
        result["include_submodules"] = click.confirm(
            f"  Include git submodules? ({scan.submodule_count} found)",
            default=False,
        )
    else:
        result["include_submodules"] = False


def _prompt_run_mode(
    console: Console,
    result: dict[str, Any],
    *,
    allow_fast: bool,
    is_large: bool,
) -> None:
    """Run-mode section (large-repo scale). Only offered for single-repo init."""
    # Fast mode = quick graph + essential-git index, no LLM docs. Suggested
    # by default on large repos; off otherwise. Only offered for single-repo
    # init (allow_fast); the workspace path leaves this untouched.
    if allow_fast:
        console.print()
        console.print(f"  [{BRAND}]Run mode[/]")
        console.print(
            "  [dim]standard = full depth · fast = quick graph + essential git, no LLM docs[/dim]"
        )
        result["run_mode"] = click.prompt(
            "  Run mode",
            default="fast" if is_large else "standard",
            type=click.Choice(["standard", "fast"]),
        )
    else:
        result["run_mode"] = "standard"


def _prompt_exclude(
    console: Console, scan: RepoScanInfo | None, result: dict[str, Any]
) -> list[str]:
    """Exclude-patterns section. Returns the parsed pattern list."""
    console.print()
    console.print(f"  [{BRAND}]Exclude Patterns[/]")

    # Show suggestions from large dirs
    if scan and scan.large_dirs:
        suggestions = scan.large_dirs[:5]
        console.print("  [dim]Large directories detected:[/dim]")
        for dirname, count in suggestions:
            console.print(f"    [dim]{dirname}/[/dim] [dim]({count:,} files)[/dim]")
        console.print()

    console.print("  [dim]Gitignore-style patterns, comma-separated or one per line.[/dim]")
    console.print("  [dim]Press Enter with empty input to finish.[/dim]")
    patterns: list[str] = []
    seen_patterns: set[str] = set()
    while True:
        raw = click.prompt("  Pattern", default="", show_default=False)
        raw = raw.strip()
        if not raw:
            break
        # Support comma-separated input; dedupe so re-pasting / re-entering
        # the same suggestions doesn't bloat the summary panel.
        for part in raw.split(","):
            part = part.strip()
            if part and part not in seen_patterns:
                seen_patterns.add(part)
                patterns.append(part)
    result["exclude"] = tuple(patterns)
    return patterns


def _prompt_git(console: Console, scan: RepoScanInfo | None, result: dict[str, Any]) -> None:
    """Git-analysis section: commit limit + rename following."""
    console.print()
    console.print(f"  [{BRAND}]Git Analysis[/]")
    commit_hint = ""
    if scan and scan.total_commits:
        commit_hint = f" [dim](repo has ~{scan.total_commits:,} total commits)[/dim]"
    console.print(f"  [dim]Controls how deeply git history is analyzed[/dim]{commit_hint}")
    console.print()

    # Smart default based on repo size
    default_limit = 500
    if scan:
        if scan.total_files < 500:
            default_limit = 1000
        elif scan.total_files > 5000:
            default_limit = 200

    val = click.prompt(
        "  Max commits per file",
        default=default_limit,
        type=int,
    )
    val = max(1, min(val, 5000))
    result["commit_limit"] = val

    result["follow_renames"] = click.confirm(
        "  Track files across git renames? (slower but more accurate)",
        default=False,
    )


def _prompt_generation(
    console: Console,
    scan: RepoScanInfo | None,
    result: dict[str, Any],
    *,
    allow_fast: bool,
    is_large: bool,
    prompt_reasoning: bool = True,
) -> None:
    """Generation section: concurrency, reasoning, embedder, test run, tiering."""
    console.print()
    console.print(f"  [{BRAND}]Generation[/]")
    console.print("  [dim]LLM page generation settings[/dim]")
    console.print()

    # Smart concurrency default
    default_concurrency = 5
    if scan and scan.total_files < 200:
        default_concurrency = 8
    elif scan and scan.total_files > 5000:
        default_concurrency = 3

    result["concurrency"] = click.prompt(
        "  Max concurrent LLM calls",
        default=default_concurrency,
        type=int,
    )

    if prompt_reasoning:
        result["reasoning"] = click.prompt(
            "  Reasoning mode",
            default="auto",
            type=click.Choice(REASONING_MODES),
        )
    else:
        result["reasoning"] = None

    # Embedder selection
    detected_embedder = _resolve_embedder_from_env()
    embedder_choices = ["gemini", "openai", "openrouter", "ollama", "mock"]
    result["embedder"] = click.prompt(
        "  Embedder for RAG",
        default=detected_embedder,
        type=click.Choice(embedder_choices),
    )

    result["test_run"] = click.confirm(
        "  Test run? (full ingestion; LLM page generation limited to top 10 files for quick validation)",
        default=False,
    )

    # Tiered doc generation: cap the number of full-LLM (tier-1) file pages on
    # large repos. The long tail is rendered from a deterministic template +
    # embedded for search (no LLM). 0 = no cap (every selected page is tier-1).
    # Only meaningful when docs actually generate (standard mode).
    result["tier1_top_n"] = None
    if allow_fast and result["run_mode"] == "standard":
        tier_default = 300 if is_large else 0
        console.print()
        console.print("  [dim]Tiered docs: cap full-LLM file pages; rest are template-only.[/dim]")
        tier_val = click.prompt(
            "  Full-LLM file-page cap (tier-1, 0 = no cap)",
            default=tier_default,
            type=int,
        )
        result["tier1_top_n"] = tier_val if tier_val > 0 else None


def _build_summary_table(result: dict[str, Any], patterns: list[str], *, allow_fast: bool) -> Table:
    """Build the configuration-summary table from the gathered answers."""
    summary = Table(box=None, padding=(0, 2), show_header=False)
    summary.add_column("Option", style="dim")
    summary.add_column("Value", style="bold")
    summary.add_row("Skip tests", "yes" if result["skip_tests"] else "no")
    summary.add_row("Skip infra", "yes" if result["skip_infra"] else "no")
    if result["include_submodules"]:
        summary.add_row("Include submodules", "yes")
    summary.add_row("Commit limit", str(result["commit_limit"]))
    summary.add_row("Follow renames", "yes" if result["follow_renames"] else "no")
    summary.add_row("Concurrency", str(result["concurrency"]))
    if result.get("reasoning"):
        summary.add_row("Reasoning", result["reasoning"])
    summary.add_row("Embedder", result["embedder"])
    if allow_fast:
        summary.add_row("Run mode", result["run_mode"])
        if result.get("tier1_top_n"):
            summary.add_row("Full-LLM page cap", str(result["tier1_top_n"]))
    if patterns:
        if len(patterns) <= 5:
            summary.add_row("Exclude", ", ".join(patterns))
        else:
            # Bullet-list when many patterns — comma-joined wraps unreadably.
            summary.add_row("Exclude", "\n".join(f"• {p}" for p in patterns))
    summary.add_row("Test run", "yes" if result["test_run"] else "no")
    return summary


def interactive_advanced_config(
    console: Console,
    scan: RepoScanInfo | None = None,
    *,
    allow_fast: bool = False,
    prompt_reasoning: bool = True,
) -> dict[str, Any]:
    """Prompt for advanced init options, grouped into logical sections.

    When *scan* is provided, uses it for smart defaults and contextual hints
    (file counts, suggested exclude patterns, etc.).

    Returns a dict with keys matching init_command kwargs:
    ``commit_limit``, ``follow_renames``, ``skip_tests``, ``skip_infra``,
    ``concurrency``, ``exclude``, ``test_run``, ``embedder``,
    ``include_submodules``.

    Editor integration prompts are intentionally not asked here so that full and
    advanced modes stay aligned. Editor setup owns those prompts after mode
    selection.
    """
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Advanced Configuration[/]",
            style=DIM,
        )
    )

    result: dict[str, Any] = {}
    is_large = bool(scan and scan.total_files > LARGE_REPO_FILE_THRESHOLD)

    _prompt_scope(console, scan, result)
    _prompt_run_mode(console, result, allow_fast=allow_fast, is_large=is_large)
    patterns = _prompt_exclude(console, scan, result)
    _prompt_git(console, scan, result)
    _prompt_generation(
        console,
        scan,
        result,
        allow_fast=allow_fast,
        is_large=is_large,
        prompt_reasoning=prompt_reasoning,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    console.print()
    summary = _build_summary_table(result, patterns, allow_fast=allow_fast)
    console.print(
        Panel(
            summary,
            title="[bold]Configuration Summary[/bold]",
            border_style=BRAND,
            padding=(0, 1),
        )
    )
    console.print()
    return result


def _resolve_embedder_from_env() -> str:
    """Auto-detect embedder from env vars (for advanced config default)."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("OLLAMA_EMBEDDING_MODEL"):
        return "ollama"
    return "mock"


def print_index_only_intro(console: Console, has_provider: bool = False) -> None:
    """Show what index-only mode will do before starting."""
    lines = [
        "  [green]✓[/] Parse all source files (AST)",
        "  [green]✓[/] Build dependency graph (PageRank, communities)",
        "  [green]✓[/] Index git history (hotspots, ownership, co-changes)",
        "  [green]✓[/] Detect dead code",
        "  [green]✓[/] Extract architectural decisions",
        "  [green]✓[/] Set up MCP server for AI assistants",
    ]
    if has_provider:
        lines.append(
            "  [green]✓[/] [dim]Decision extraction enhanced (provider key detected)[/dim]"
        )
    lines.append("")
    lines.append("  [dim]No LLM calls. No cost.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Index Only[/bold]",
            border_style=BRAND,
            padding=(1, 1),
        )
    )
    console.print()
